#!/usr/bin/env python3
"""Point d'entrée unique pour le déploiement Scaleway d'edumatch-cicd.

Remplace la séquence manuelle décrite en prose dans `README.md` et
`terraform/README.md` par des sous-commandes rejouables. Ce script ne
remplace aucune décision : il exécute, dans l'ordre, exactement ce que ces
deux fichiers documentent déjà, et refuse de continuer quand une étape
préalable manque (plan absent, plan périmé, confirmation non donnée).

Bibliothèque standard uniquement — aucune dépendance à installer. Fonctionne
sous Windows (Git Bash, PowerShell) comme sous Linux : tous les appels de
processus sont faits par liste d'arguments (`shell=False`), jamais par une
chaîne interprétée par un shell.

Exemple d'usage :

    python scripts/deploiement.py verifier
    python scripts/deploiement.py amorcer
    python scripts/deploiement.py plan
    python scripts/deploiement.py appliquer --oui
    python scripts/deploiement.py kubeconfig
    python scripts/deploiement.py secrets-k8s
    python scripts/deploiement.py deployer --image-tag 3f9c1a2
    python scripts/deploiement.py monitoring
    python scripts/deploiement.py detruire

Aucun secret n'est jamais journalisé : les commandes affichées avant
exécution masquent systématiquement les valeurs issues des variables
d'environnement énumérées dans `VARIABLES_SECRETES`.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets as secrets_module
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

# ─── Emplacements, relatifs à ce fichier — jamais de chemin en dur ────────
RACINE_DEPOT = Path(__file__).resolve().parent.parent
DOSSIER_TERRAFORM = RACINE_DEPOT / "terraform"
DOSSIER_K8S_BASE = RACINE_DEPOT / "k8s" / "base"
DOSSIER_MONITORING = RACINE_DEPOT / "monitoring"

VERSION_TERRAFORM_EXIGEE = "1.10.5"
NAMESPACE_APPLICATIF = "edumatch"
NAMESPACE_MONITORING = "monitoring"
NOM_BUCKET_ETAT = "edumatch-tfstate"
REGION = "fr-par"

# Variables dont la valeur ne doit JAMAIS apparaître, même partiellement,
# dans une sortie de ce script (journal, tableau, commande affichée).
VARIABLES_SECRETES = ("SCW_SECRET_KEY", "AWS_SECRET_ACCESS_KEY")
# Variables dont seuls les 6 premiers caractères peuvent être montrés — ce
# sont des identifiants publics (clé d'accès, identifiant de projet), pas
# des secrets au sens strict, mais on reste prudent sur la longueur montrée.
VARIABLES_IDENTIFIANTS_PUBLICS = (
    "SCW_ACCESS_KEY",
    "SCW_DEFAULT_PROJECT_ID",
    "AWS_ACCESS_KEY_ID",
)
VARIABLES_REQUISES = VARIABLES_SECRETES + VARIABLES_IDENTIFIANTS_PUBLICS


class ErreurDeploiement(RuntimeError):
    """Erreur qui doit interrompre le script avec un message actionnable."""


def afficher(message: str) -> None:
    """Point d'affichage unique — un script d'exploitation, pas une lib."""
    print(message)


def afficher_titre(titre: str) -> None:
    afficher("")
    afficher(f"── {titre} " + "─" * max(0, 60 - len(titre)))


def _redacter(valeur: str) -> str:
    """Ne jamais montrer une valeur de secret, même dans un message d'erreur."""
    return "***"


def commande_affichable(commande: Sequence[str], secrets_a_masquer: Iterable[str] = ()) -> str:
    """Construit la version journalisable d'une commande, secrets masqués.

    `secrets_a_masquer` contient des valeurs exactes (pas des noms de
    variable) à remplacer par `***` si elles apparaissent telles quelles
    dans un argument.
    """
    secrets_non_vides = [s for s in secrets_a_masquer if s]
    parties = []
    for arg in commande:
        arg_affiche = arg
        for secret in secrets_non_vides:
            if secret and secret in arg_affiche:
                arg_affiche = arg_affiche.replace(secret, "***")
        parties.append(arg_affiche)
    return " ".join(parties)


def executer(
    commande: Sequence[str],
    *,
    cwd: Path | None = None,
    entree: str | None = None,
    verifier_code: bool = True,
    secrets_a_masquer: Iterable[str] = (),
    conseil_en_cas_echec: str = "",
) -> subprocess.CompletedProcess:
    """Exécute une commande externe, l'affiche d'abord (secrets masqués).

    Le script s'arrête au premier échec avec un message qui dit quoi faire,
    sauf si `verifier_code` vaut False (l'appelant gère alors le code de
    retour lui-même — utile pour `kubectl diff`, dont le code 1 signifie
    "des différences existent", pas une erreur).
    """
    afficher(f"$ {commande_affichable(commande, secrets_a_masquer)}")
    resultat = subprocess.run(
        list(commande),
        cwd=str(cwd) if cwd else None,
        input=entree,
        text=True,
        shell=False,
    )
    if verifier_code and resultat.returncode != 0:
        message = (
            f"Échec de la commande ci-dessus (code {resultat.returncode})."
        )
        if conseil_en_cas_echec:
            message += f"\nÀ faire : {conseil_en_cas_echec}"
        raise ErreurDeploiement(message)
    return resultat


def capturer(
    commande: Sequence[str],
    *,
    cwd: Path | None = None,
    secrets_a_masquer: Iterable[str] = (),
) -> str:
    """Exécute une commande et retourne sa sortie standard, sans l'afficher.

    Utilisé quand la sortie elle-même pourrait contenir un secret (par
    exemple un manifeste Kubernetes généré avec `--dry-run=client -o yaml`
    pour un objet Secret) : la commande est journalisée, pas son résultat.
    """
    afficher(f"$ {commande_affichable(commande, secrets_a_masquer)}")
    resultat = subprocess.run(
        list(commande),
        cwd=str(cwd) if cwd else None,
        text=True,
        shell=False,
        capture_output=True,
    )
    if resultat.returncode != 0:
        afficher(resultat.stderr.strip())
        raise ErreurDeploiement(
            f"Échec de la commande ci-dessus (code {resultat.returncode})."
        )
    return resultat.stdout


# ─── Chargement de l'environnement ─────────────────────────────────────────


def charger_fichier_env(chemin: Path) -> None:
    """Charge des paires `CLE=VALEUR` dans l'environnement du processus.

    Ne remplace jamais une variable déjà présente dans l'environnement du
    terminal appelant : un `export` fait à la main avant de lancer le
    script a toujours la priorité sur le fichier. C'est le même
    comportement que la plupart des chargeurs de `.env` — la surprise
    inverse (le fichier écrase un export explicite) serait plus dangereuse.
    """
    if not chemin.is_file():
        return
    for ligne_brute in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne_brute.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        cle = cle.strip()
        valeur = valeur.strip().strip('"').strip("'")
        if cle and cle not in os.environ:
            os.environ[cle] = valeur

    # Confort documenté dans terraform/versions.tf et le README racine : le
    # backend Terraform "s3" lit les identifiants sous des noms AWS, mêmes
    # valeurs que les clés API Scaleway. On évite de les faire écrire deux
    # fois dans scaleway.env.example.
    if "AWS_ACCESS_KEY_ID" not in os.environ and os.environ.get("SCW_ACCESS_KEY"):
        os.environ["AWS_ACCESS_KEY_ID"] = os.environ["SCW_ACCESS_KEY"]
    if "AWS_SECRET_ACCESS_KEY" not in os.environ and os.environ.get("SCW_SECRET_KEY"):
        os.environ["AWS_SECRET_ACCESS_KEY"] = os.environ["SCW_SECRET_KEY"]


# ─── verifier ───────────────────────────────────────────────────────────


def _version_terraform() -> str | None:
    chemin = shutil.which("terraform")
    if not chemin:
        return None
    resultat = subprocess.run(
        [chemin, "-version"], text=True, capture_output=True, shell=False
    )
    premiere_ligne = resultat.stdout.splitlines()[0] if resultat.stdout else ""
    return premiere_ligne.strip()


def cmd_verifier(_: argparse.Namespace) -> int:
    afficher_titre("Vérification des outils et des variables")
    ok = True

    lignes_outils: list[tuple[str, str, bool]] = []

    version_tf = _version_terraform()
    if version_tf is None:
        lignes_outils.append(("terraform", "absent du PATH", False))
        ok = False
    else:
        conforme = VERSION_TERRAFORM_EXIGEE in version_tf
        lignes_outils.append((
            "terraform",
            f"{version_tf}" + ("" if conforme else f" (exigé : {VERSION_TERRAFORM_EXIGEE})"),
            conforme,
        ))
        ok = ok and conforme

    for outil, arguments_version in (
        ("scw", ["version"]),
        ("kubectl", ["version", "--client", "--output=yaml"]),
        ("docker", ["--version"]),
    ):
        chemin = shutil.which(outil)
        if not chemin:
            lignes_outils.append((outil, "absent du PATH", False))
            ok = False
            continue
        resultat = subprocess.run(
            [chemin, *arguments_version], text=True, capture_output=True, shell=False
        )
        premiere_ligne = (resultat.stdout or resultat.stderr).splitlines()
        detail = premiere_ligne[0].strip() if premiere_ligne else "version inconnue"
        lignes_outils.append((outil, detail, True))

    afficher(f"{'Outil':<12}{'État':<45}{'OK'}")
    for nom, detail, statut in lignes_outils:
        afficher(f"{nom:<12}{detail:<45}{'oui' if statut else 'NON'}")

    afficher("")
    afficher(f"{'Variable':<28}{'Présente':<12}{'Aperçu'}")
    for nom in VARIABLES_REQUISES:
        valeur = os.environ.get(nom, "")
        presente = bool(valeur)
        if not presente:
            ok = False
            apercu = "—"
        elif nom in VARIABLES_SECRETES:
            apercu = "(secret, jamais affiché)"
        else:
            apercu = valeur[:6] + "…" if len(valeur) > 6 else valeur
        afficher(f"{nom:<28}{'oui' if presente else 'NON':<12}{apercu}")

    afficher("")
    if ok:
        afficher("Tout est en place.")
        return 0
    afficher(
        "Éléments manquants ci-dessus. Installer l'outil manquant, ou "
        "renseigner la variable via `scaleway.env` (copie de "
        "`scaleway.env.example`) ou `--env <chemin>`."
    )
    return 1


# ─── amorcer ────────────────────────────────────────────────────────────


def noms_des_buckets(buckets: object) -> set[str]:
    """Noms lus dans la sortie JSON de `scw object bucket list`.

    Le CLI renvoie le champ `Name`, avec une majuscule (sortie S3 brute) : lire seulement
    `name` faisait passer un bucket existant pour absent, et la création échouait en 409
    « BucketAlreadyOwnedByYou ». Les deux graphies sont acceptées.
    """
    if not isinstance(buckets, list):
        return set()
    return {b.get("Name") or b.get("name") for b in buckets if isinstance(b, dict)} - {None}


def cmd_amorcer(_: argparse.Namespace) -> int:
    afficher_titre(f"Amorçage du bucket d'état Terraform « {NOM_BUCKET_ETAT} »")
    if not shutil.which("scw"):
        raise ErreurDeploiement(
            "Le CLI `scw` est introuvable. L'installer (voir la documentation "
            "Scaleway) ou créer le bucket à la main dans la console : "
            "Object Storage → Créer un bucket → "
            f"{NOM_BUCKET_ETAT}, région {REGION}, visibilité privée."
        )
    sortie = capturer(["scw", "object", "bucket", "list", f"region={REGION}", "-o", "json"])
    try:
        buckets = json.loads(sortie)
    except json.JSONDecodeError as exc:
        raise ErreurDeploiement(
            "Réponse inattendue de `scw object bucket list` — vérifier la "
            "configuration du CLI (`scw init`)."
        ) from exc
    if NOM_BUCKET_ETAT in noms_des_buckets(buckets):
        afficher(f"Le bucket « {NOM_BUCKET_ETAT} » existe déjà — rien à faire (idempotent).")
        return 0
    executer(
        ["scw", "object", "bucket", "create", f"name={NOM_BUCKET_ETAT}", f"region={REGION}"],
        conseil_en_cas_echec="vérifier les droits du projet Scaleway courant (SCW_DEFAULT_PROJECT_ID).",
    )
    afficher(f"Bucket « {NOM_BUCKET_ETAT} » créé.")
    return 0


# ─── plan ───────────────────────────────────────────────────────────────


def cmd_plan(args: argparse.Namespace) -> int:
    afficher_titre("Plan Terraform")
    if not (DOSSIER_TERRAFORM / ".terraform").is_dir():
        executer(
            ["terraform", "init"],
            cwd=DOSSIER_TERRAFORM,
            conseil_en_cas_echec=(
                "vérifier que le bucket d'état existe (`deploiement.py amorcer`) "
                "et que SCW_ACCESS_KEY / SCW_SECRET_KEY sont exportées."
            ),
        )
    else:
        afficher("`.terraform/` déjà initialisé — `terraform init` non relancé.")

    executer(
        ["terraform", "fmt", "-check", "-recursive"],
        cwd=DOSSIER_TERRAFORM,
        conseil_en_cas_echec="lancer `terraform fmt -recursive` puis relire le diff avant de continuer.",
    )
    executer(["terraform", "validate"], cwd=DOSSIER_TERRAFORM)

    fichier_plan = args.out
    executer(
        ["terraform", "plan", f"-out={fichier_plan}"],
        cwd=DOSSIER_TERRAFORM,
    )
    afficher("")
    afficher(
        f"Plan écrit dans terraform/{fichier_plan}. "
        "À LIRE intégralement avant `appliquer` — ce script ne l'a pas "
        "évalué à votre place."
    )
    return 0


# ─── appliquer ──────────────────────────────────────────────────────────


def _plan_est_a_jour(fichier_plan: Path) -> bool:
    if not fichier_plan.is_file():
        return False
    date_plan = fichier_plan.stat().st_mtime
    fichiers_tf = list(DOSSIER_TERRAFORM.glob("*.tf")) + list(
        (DOSSIER_TERRAFORM / "cloud-init").glob("*")
    )
    return all(f.stat().st_mtime <= date_plan for f in fichiers_tf if f.is_file())


def cmd_appliquer(args: argparse.Namespace) -> int:
    afficher_titre("Application du plan Terraform")
    fichier_plan = DOSSIER_TERRAFORM / args.plan
    if not fichier_plan.is_file():
        raise ErreurDeploiement(
            f"Aucun fichier de plan « terraform/{args.plan} ». "
            "Lancer `deploiement.py plan` d'abord."
        )
    if not _plan_est_a_jour(fichier_plan):
        raise ErreurDeploiement(
            f"« terraform/{args.plan} » est plus vieux qu'au moins un fichier "
            ".tf (ou du dossier cloud-init/) : le plan ne correspond peut-être "
            "plus au code. Relancer `deploiement.py plan` puis relire le "
            "nouveau plan avant d'appliquer."
        )

    if not args.oui:
        reponse = input(
            f"Appliquer « terraform/{args.plan} » sur le projet Scaleway "
            f"{os.environ.get('SCW_DEFAULT_PROJECT_ID', '(inconnu)')[:6]}… ? "
            "Ce plan a-t-il été lu en entier ? [oui/N] "
        )
        if reponse.strip().lower() not in {"oui", "o", "yes", "y"}:
            afficher("Annulé — le plan n'a pas été appliqué.")
            return 1

    executer(["terraform", "apply", args.plan], cwd=DOSSIER_TERRAFORM)

    sortie_json = capturer(["terraform", "output", "-json"], cwd=DOSSIER_TERRAFORM)
    sorties = json.loads(sortie_json)
    afficher("")
    afficher("Sorties utiles :")
    for nom in ("cluster_id", "registre_endpoint", "bucket_artefacts"):
        if nom in sorties:
            afficher(f"  {nom} = {sorties[nom].get('value')}")
    return 0


# ─── kubeconfig ─────────────────────────────────────────────────────────


def cmd_kubeconfig(_: argparse.Namespace) -> int:
    afficher_titre("Récupération et installation du kubeconfig")
    cluster_id = capturer(
        ["terraform", "output", "-raw", "cluster_id"], cwd=DOSSIER_TERRAFORM
    ).strip()
    if not cluster_id:
        raise ErreurDeploiement(
            "Aucune sortie `cluster_id` — le cluster a-t-il été créé "
            "(`deploiement.py appliquer`) ?"
        )
    # Terraform renvoie l'identifiant préfixé par sa région (« fr-par/<uuid> »),
    # alors que le CLI attend l'identifiant seul dès lors que la région lui est
    # passée à part : le préfixe conservé produit une erreur 404.
    identifiant = cluster_id.rsplit("/", 1)[-1]
    executer(["scw", "k8s", "kubeconfig", "install", identifiant, f"region={REGION}"])
    executer(["kubectl", "get", "nodes"], conseil_en_cas_echec="le kubeconfig installé pointe-t-il le bon cluster ?")

    afficher("")
    afficher(
        "Pour publier le secret GitHub Actions SCW_KUBECONFIG_B64, exécuter "
        "vous-même la commande suivante dans un terminal — ce script ne "
        "l'exécute pas et n'affiche jamais la valeur encodée qu'elle produit :"
    )
    afficher(f"  scw k8s kubeconfig get {identifiant} region={REGION} | base64 -w0")
    afficher(
        "  (PowerShell : [Convert]::ToBase64String([IO.File]::ReadAllBytes(\"kubeconfig.yaml\")))"
    )
    afficher(
        "Coller le résultat dans Settings → Secrets and variables → Actions "
        "→ SCW_KUBECONFIG_B64, du dépôt edumatch-cicd."
    )
    return 0


# ─── secrets-k8s ────────────────────────────────────────────────────────


def _appliquer_secret_genere(commande_creation: Sequence[str], secrets_a_masquer: Sequence[str]) -> None:
    """Génère un manifeste de Secret en mémoire puis l'applique — idempotent.

    Le YAML produit par `--dry-run=client -o yaml` contient le secret encodé
    en base64 : il n'est jamais affiché, seule la commande (secrets masqués)
    l'est.
    """
    manifeste = capturer(commande_creation, secrets_a_masquer=secrets_a_masquer)
    executer(["kubectl", "apply", "-f", "-"], entree=manifeste)


def cmd_secrets_k8s(_: argparse.Namespace) -> int:
    afficher_titre("Création des secrets Kubernetes (namespace applicatif)")
    for nom in ("SCW_ACCESS_KEY", "SCW_SECRET_KEY"):
        if not os.environ.get(nom):
            raise ErreurDeploiement(
                f"{nom} n'est pas définie — lancer `deploiement.py verifier` d'abord."
            )

    manifeste_namespace = capturer(
        ["kubectl", "create", "namespace", NAMESPACE_APPLICATIF, "--dry-run=client", "-o", "yaml"]
    )
    executer(["kubectl", "apply", "-f", "-"], entree=manifeste_namespace)

    acces = os.environ["SCW_ACCESS_KEY"]
    secret_ = os.environ["SCW_SECRET_KEY"]

    _appliquer_secret_genere(
        [
            "kubectl", "create", "secret", "generic", "edumatch-object-storage",
            "--namespace", NAMESPACE_APPLICATIF,
            f"--from-literal=access-key-id={acces}",
            f"--from-literal=secret-access-key={secret_}",
            "--dry-run=client", "-o", "yaml",
        ],
        secrets_a_masquer=[acces, secret_],
    )

    registre = os.environ.get("SCW_REGISTRY_ENDPOINT")
    if not registre:
        registre = capturer(
            ["terraform", "output", "-raw", "registre_endpoint"], cwd=DOSSIER_TERRAFORM
        ).strip()
    if not registre:
        raise ErreurDeploiement(
            "Aucun registre connu : définir SCW_REGISTRY_ENDPOINT ou exécuter "
            "`deploiement.py appliquer` d'abord."
        )

    _appliquer_secret_genere(
        [
            "kubectl", "create", "secret", "docker-registry", "edumatch-registry-pull",
            "--namespace", NAMESPACE_APPLICATIF,
            f"--docker-server={registre}",
            "--docker-username=nologin",
            f"--docker-password={secret_}",
            "--dry-run=client", "-o", "yaml",
        ],
        secrets_a_masquer=[secret_],
    )

    afficher("Secrets créés ou mis à jour (idempotent).")
    return 0


# ─── deployer ───────────────────────────────────────────────────────────

# Ordre d'application : le namespace et les objets de configuration avant
# le Deployment qui en dépend, le Service et le HPA après.
ORDRE_MANIFESTES_BASE = (
    "namespace.yaml",
    "serviceaccount.yaml",
    "configmap.yaml",
    "networkpolicy.yaml",
    "pdb.yaml",
    "deployment.yaml",
    "service.yaml",
    "hpa.yaml",
)


def _manifestes_substitues(tag_image: str, registre: str) -> str:
    image = f"{registre}/edumatch-serve:{tag_image}"
    documents = []
    for nom in ORDRE_MANIFESTES_BASE:
        chemin = DOSSIER_K8S_BASE / nom
        if not chemin.is_file():
            raise ErreurDeploiement(f"Manifeste attendu introuvable : {chemin}")
        contenu = chemin.read_text(encoding="utf-8")
        contenu = contenu.replace("__EDUMATCH_SERVE_IMAGE__", image)
        documents.append(contenu)
    return "\n---\n".join(documents)


def cmd_deployer(args: argparse.Namespace) -> int:
    afficher_titre(f"Déploiement de l'image tag « {args.image_tag} »")
    if not args.image_tag or args.image_tag == "latest":
        raise ErreurDeploiement(
            "Un tag `latest` n'est jamais déployé — passer l'empreinte courte "
            "du commit publiée par le workflow build-images.yml."
        )

    registre = os.environ.get("SCW_REGISTRY_ENDPOINT")
    if not registre:
        registre = capturer(
            ["terraform", "output", "-raw", "registre_endpoint"], cwd=DOSSIER_TERRAFORM
        ).strip()
    if not registre:
        raise ErreurDeploiement(
            "Aucun registre connu : définir SCW_REGISTRY_ENDPOINT ou exécuter "
            "`deploiement.py appliquer` d'abord."
        )

    manifestes = _manifestes_substitues(args.image_tag, registre)

    afficher("Différences par rapport au cluster (kubectl diff) :")
    resultat_diff = executer(
        ["kubectl", "diff", "-f", "-"],
        entree=manifestes,
        verifier_code=False,
    )
    if resultat_diff.returncode not in (0, 1):
        raise ErreurDeploiement(
            f"`kubectl diff` a échoué (code {resultat_diff.returncode}), pas "
            "seulement signalé une différence — vérifier la connexion au cluster."
        )

    executer(["kubectl", "apply", "-f", "-"], entree=manifestes)
    executer(
        [
            "kubectl", "rollout", "status", "deployment/edumatch-serve",
            "--namespace", NAMESPACE_APPLICATIF, "--timeout=180s",
        ],
        conseil_en_cas_echec=(
            "`kubectl get pods -n edumatch` puis `kubectl describe pod ...` "
            "pour voir pourquoi le déploiement ne converge pas."
        ),
    )
    afficher("Déploiement terminé et confirmé prêt.")
    return 0


# ─── monitoring ─────────────────────────────────────────────────────────


def cmd_monitoring(_: argparse.Namespace) -> int:
    afficher_titre("Déploiement de la supervision (Prometheus, Alertmanager, Grafana)")

    fichiers_prometheus = ("namespace.yaml", "rbac.yaml", "configmap.yaml", "alerts.yaml", "deployment.yaml")
    for nom in fichiers_prometheus:
        executer(["kubectl", "apply", "-f", str(DOSSIER_MONITORING / "prometheus" / nom)])

    for nom in ("configmap.yaml", "deployment.yaml"):
        executer(["kubectl", "apply", "-f", str(DOSSIER_MONITORING / "alertmanager" / nom)])

    executer(["kubectl", "apply", "-f", str(DOSSIER_MONITORING / "grafana" / "provisioning-datasource.yaml")])
    executer(["kubectl", "apply", "-f", str(DOSSIER_MONITORING / "grafana" / "provisioning-dashboards.yaml")])

    verification = subprocess.run(
        ["kubectl", "get", "secret", "grafana-admin", "--namespace", NAMESPACE_MONITORING],
        capture_output=True,
        text=True,
        shell=False,
    )
    if verification.returncode == 0:
        afficher("Le secret « grafana-admin » existe déjà — mot de passe conservé (idempotent).")
    else:
        mot_de_passe = secrets_module.token_urlsafe(24)
        manifeste = capturer(
            [
                "kubectl", "create", "secret", "generic", "grafana-admin",
                "--namespace", NAMESPACE_MONITORING,
                f"--from-literal=admin-password={mot_de_passe}",
                "--dry-run=client", "-o", "yaml",
            ],
            secrets_a_masquer=[mot_de_passe],
        )
        executer(["kubectl", "apply", "-f", "-"], entree=manifeste)
        afficher(
            "Secret « grafana-admin » créé. Pour relire le mot de passe : "
            "kubectl get secret grafana-admin -n monitoring "
            "-o jsonpath='{.data.admin-password}' | base64 -d"
        )

    manifeste_dashboard = capturer(
        [
            "kubectl", "create", "configmap", "grafana-dashboard-edumatch",
            "--namespace", NAMESPACE_MONITORING,
            f"--from-file=edumatch-api.json={DOSSIER_MONITORING / 'grafana' / 'dashboards' / 'edumatch-api.json'}",
            "--dry-run=client", "-o", "yaml",
        ]
    )
    executer(["kubectl", "apply", "-f", "-"], entree=manifeste_dashboard)

    executer(["kubectl", "apply", "-f", str(DOSSIER_MONITORING / "grafana" / "deployment.yaml")])

    afficher("")
    afficher(
        "Vérifier : kubectl get pods -n monitoring -w ; puis port-forward "
        "prometheus (9090), alertmanager (9093), grafana (3000) — voir "
        "monitoring/README.md, section « Déployer »."
    )
    return 0


# ─── detruire ───────────────────────────────────────────────────────────


def cmd_detruire(args: argparse.Namespace) -> int:
    afficher_titre("Destruction de l'infrastructure de démonstration")
    afficher(
        "Rappel de la règle de coût : le cluster n'est provisionné que pour "
        "les démonstrations. Un cluster oublié coûte de l'ordre de 15 à "
        "30 EUR pour un mois — cette commande existe pour éviter exactement ça."
    )
    if not args.oui:
        reponse = input(
            "Confirmer la destruction complète (namespaces + infrastructure "
            "Terraform) ? [oui/N] "
        )
        if reponse.strip().lower() not in {"oui", "o", "yes", "y"}:
            afficher("Annulé.")
            return 1

    for namespace in (NAMESPACE_MONITORING, NAMESPACE_APPLICATIF):
        executer(["kubectl", "delete", "namespace", namespace, "--ignore-not-found"])

    fichier_destroy = "destroy.tfout"
    executer(
        ["terraform", "plan", "-destroy", f"-out={fichier_destroy}"],
        cwd=DOSSIER_TERRAFORM,
    )
    afficher(f"Plan de destruction écrit dans terraform/{fichier_destroy} — À LIRE.")
    if not args.oui:
        reponse = input("Appliquer ce plan de destruction maintenant ? [oui/N] ")
        if reponse.strip().lower() not in {"oui", "o", "yes", "y"}:
            afficher("Destruction Terraform non appliquée — relancer manuellement quand prêt :")
            afficher(f"  terraform apply {fichier_destroy}   (depuis terraform/)")
            return 1
    executer(["terraform", "apply", fichier_destroy], cwd=DOSSIER_TERRAFORM)
    afficher("Infrastructure détruite.")
    return 0


# ─── tout ───────────────────────────────────────────────────────────────


def cmd_tout(args: argparse.Namespace) -> int:
    afficher_titre("Séquence complète jusqu'au plan (jamais d'application automatique)")
    code = cmd_verifier(args)
    if code != 0:
        return code
    cmd_amorcer(args)
    args.out = getattr(args, "out", "plan.tfout")
    cmd_plan(args)
    afficher("")
    afficher(
        "Arrêt volontaire ici : lire terraform/plan.tfout en entier, puis "
        "lancer `deploiement.py appliquer` explicitement. Ce script "
        "n'enchaîne jamais un apply sans relecture humaine du plan."
    )
    return 0


# ─── argparse ───────────────────────────────────────────────────────────


def construire_analyseur() -> argparse.ArgumentParser:
    analyseur = argparse.ArgumentParser(
        prog="deploiement.py",
        description=(
            "Automatise le déploiement Scaleway d'edumatch-cicd — cluster "
            "Kapsule, registre, secrets Kubernetes, manifestes applicatifs "
            "et supervision. Voir scripts/README.md pour la séquence complète."
        ),
    )
    analyseur.add_argument(
        "--env",
        type=Path,
        default=None,
        help=(
            "Chemin vers un fichier de variables d'environnement à charger "
            "(format scaleway.env.example). Par défaut : scaleway.env à la "
            "racine du dépôt, s'il existe."
        ),
    )
    sous_analyseurs = analyseur.add_subparsers(dest="sous_commande", required=True)

    sous_analyseurs.add_parser(
        "verifier", help="Contrôle les outils requis et les variables d'environnement."
    ).set_defaults(fonction=cmd_verifier)

    sous_analyseurs.add_parser(
        "amorcer", help="Crée le bucket d'état Terraform s'il n'existe pas."
    ).set_defaults(fonction=cmd_amorcer)

    p_plan = sous_analyseurs.add_parser(
        "plan", help="terraform init/fmt/validate/plan, écrit un fichier de plan."
    )
    p_plan.add_argument("--out", default="plan.tfout", help="Nom du fichier de plan produit.")
    p_plan.set_defaults(fonction=cmd_plan)

    p_appliquer = sous_analyseurs.add_parser(
        "appliquer", help="Applique un plan existant et à jour, après confirmation."
    )
    p_appliquer.add_argument("--plan", default="plan.tfout", help="Fichier de plan à appliquer.")
    p_appliquer.add_argument("--oui", action="store_true", help="Ne pas demander de confirmation interactive.")
    p_appliquer.set_defaults(fonction=cmd_appliquer)

    sous_analyseurs.add_parser(
        "kubeconfig", help="Récupère et installe le kubeconfig du cluster."
    ).set_defaults(fonction=cmd_kubeconfig)

    sous_analyseurs.add_parser(
        "secrets-k8s", help="Crée le namespace et les secrets Kubernetes depuis l'environnement."
    ).set_defaults(fonction=cmd_secrets_k8s)

    p_deployer = sous_analyseurs.add_parser(
        "deployer", help="Applique k8s/base/ avec le tag d'image indiqué."
    )
    p_deployer.add_argument("--image-tag", required=True, help="Empreinte courte du commit à déployer.")
    p_deployer.set_defaults(fonction=cmd_deployer)

    sous_analyseurs.add_parser(
        "monitoring", help="Applique les manifestes de monitoring/ dans l'ordre documenté."
    ).set_defaults(fonction=cmd_monitoring)

    p_detruire = sous_analyseurs.add_parser(
        "detruire", help="Supprime les namespaces puis détruit l'infrastructure Terraform."
    )
    p_detruire.add_argument("--oui", action="store_true", help="Ne pas demander de confirmation interactive.")
    p_detruire.set_defaults(fonction=cmd_detruire)

    p_tout = sous_analyseurs.add_parser(
        "tout", help="verifier + amorcer + plan, puis s'arrête pour lecture du plan."
    )
    p_tout.add_argument("--out", default="plan.tfout", help="Nom du fichier de plan produit.")
    p_tout.set_defaults(fonction=cmd_tout)

    return analyseur


def main(argv: list[str] | None = None) -> int:
    # La console Windows (cp1252/cp437 selon la configuration) ne sait pas
    # toujours encoder les caractères de dessin de boîte utilisés ci-dessus
    # pour les titres de section. On repasse la sortie standard en UTF-8,
    # sans jamais faire planter le script pour une question d'affichage.
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            try:
                flux.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    analyseur = construire_analyseur()
    args = analyseur.parse_args(argv)

    chemin_env = args.env if args.env else RACINE_DEPOT / "scaleway.env"
    charger_fichier_env(chemin_env)

    horodatage = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    afficher(f"[{horodatage}] deploiement.py {args.sous_commande}")

    try:
        return args.fonction(args)
    except ErreurDeploiement as erreur:
        afficher("")
        afficher(f"ERREUR : {erreur}")
        return 1
    except FileNotFoundError as erreur:
        afficher("")
        afficher(f"ERREUR : commande introuvable — {erreur}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
