# edumatch-cicd

Industrialisation du moteur de matching EduMatch-IA : intégration continue,
construction et publication des images, déploiement sur Kubernetes. Le code
métier — API, modèle, pipelines de données — vit dans un dépôt distinct,
`edumatch-ia`.

## Pourquoi deux dépôts distincts

Un même dépôt qui contient à la fois le code métier et son industrialisation
mélange deux cycles de vie différents. Le code métier change à chaque
fonctionnalité ou correction ; l'industrialisation change quand la façon de
construire, tester ou déployer évolue — beaucoup plus rarement, et pour des
raisons différentes (une clé de registre qui tourne, un cluster qui change de
taille). Les séparer donne trois choses concrètes :

- **Des permissions différentes.** Les identifiants Scaleway (registre de
  conteneurs, cluster Kubernetes) n'ont aucune raison d'être accessibles à
  quiconque peut proposer une pull request sur le code métier. Ce dépôt est
  celui qui détient ces secrets ; `edumatch-ia` ne les voit jamais.
- **Une lecture plus simple pour un tiers.** Un jury, ou un futur relecteur,
  qui veut comprendre comment le modèle est construit n'a pas à traverser des
  manifestes Kubernetes ; celui qui veut comprendre comment le service est
  déployé n'a pas à traverser le code d'entraînement.
- **Une preuve, pas une déclaration.** Le critère 4.9 du référentiel de
  certification exige deux dépôts de code distincts. Un dossier qui l'affirme
  sans que le second dépôt existe et contienne un travail réel n'est pas une
  preuve.

L'alternative écartée était un mono-dépôt avec des dossiers `app/` et
`ops/` séparés par convention. Elle coûte moins cher à mettre en place (pas de
déclenchement inter-dépôts à câbler) mais échoue sur les deux premiers points
ci-dessus, et ne répond pas au critère 4.9 tel qu'il est formulé. Le seuil qui
ferait reconsidérer ce choix : une équipe d'une seule personne, sur un projet
qui ne sera plus soumis à évaluation externe, où le coût de deux dépôts
(synchronisation, déclenchement inter-dépôts) dépasserait le bénéfice de
séparation des permissions — ce n'est pas la situation ici.

## Ce que contient ce dépôt

```
edumatch-cicd/
├── .github/workflows/
│   ├── ci.yml              intégration continue : lint (ruff) + tests (pytest)
│   ├── build-images.yml    construction et publication des images (Scaleway)
│   └── deploy.yml          déploiement des manifestes sur Kapsule (démonstration)
├── terraform/               infrastructure as code — cluster Kapsule, réseau, registre, bucket
├── k8s/                      manifestes Kubernetes — Deployment, Service, HPA, sécurité
├── monitoring/               Prometheus, Alertmanager, Grafana — SLO déclaré, alertes actionnables
├── scripts/deploiement.py   point d'entrée unique du déploiement Scaleway (voir scripts/README.md)
└── scaleway.env.example     gabarit des variables Scaleway, jamais de valeur réelle
```

`terraform/`, `k8s/` et `monitoring/` sont remplis — voir le `README.md` propre à
chacun pour le détail de chaque fichier et l'état de ce qui a été exécuté. Les trois
workflows sont complets ; `ci.yml` fonctionne sans aucun secret Scaleway. L'API expose
`/metrics` (`prometheus-fastapi-instrumentator>=8.0,<9.0` côté `edumatch-ia`) : le tableau
de bord a donc une source, il reste à l'afficher sur un cluster.

## Les trois workflows

| Workflow | Déclenchement | Ce qu'il fait |
|---|---|---|
| `ci.yml` | manuel, ou automatique sur poussée/PR d'edumatch-ia (voir câblage ci-dessous) | Récupère edumatch-ia au commit demandé, `ruff check`, `pytest` sur les échantillons versionnés (`data/samples/`, jamais un téléchargement de 4,6 Go) |
| `build-images.yml` | manuel, ou automatique sur poussée sur `main` d'edumatch-ia | Construit `edumatch-serve`, `edumatch-train` et `edumatch-airflow` depuis `edumatch-ia/docker/`, les tague par l'empreinte courte du commit, les publie sur le registre Scaleway |
| `deploy.yml` | manuel, ou sur étiquette (tag) d'edumatch-ia | Applique les manifestes de `k8s/base/` sur le cluster de démonstration Kapsule, après vérification qu'ils existent |

### État des exécutions sur la forge

`ci.yml` a tourné six fois (onglet Actions du dépôt, déclenchement manuel) : trois
exécutions rouges, trois vertes. La première a échoué au lint — huit erreurs `ruff` que la
vérification locale ne voyait pas, puisqu'elle ne lançait que `pytest`. La deuxième a
échoué aux tests, sur une erreur de configuration. Chaque échec a été corrigé en avant,
sans contournement ni désactivation de contrôle ; les trois dernières exécutions sont
vertes, lint et suite complète, la dernière sur 909 tests passés et 1 ignoré en Python
3.11.

`build-images.yml` a été exécuté le 22 septembre 2026 : les trois images sont publiées au
registre Scaleway sous l'empreinte `1acab97`. `deploy.yml` reste à exécuter : le déploiement du
22 septembre a été fait par `scripts/deploiement.py`, qui applique les mêmes manifestes.

`edumatch-airflow` est construit depuis `edumatch-ia/docker/Dockerfile.airflow`.
L'image n'embarque **ni machine virtuelle Java ni extra `[spark]`** : les
ajouter cassait le cœur d'Airflow 2.9.3 (conflit de versions de SQLAlchemy et
de pandas avec le paquet du projet), et la tâche d'agrégation Sirene tourne en
production avec le moteur Polars (`moteur_volume: local`). Elle embarque ses
DAG, sa configuration et `libgomp1`, nécessaire à LightGBM. Le détail est dans
l'ADR 0019 d'edumatch-ia, section « Amendement du 2026-09-15 ».
`build-images.yml` construit ce fichier tel qu'il existe au commit demandé.

Aucun des trois ne se déclenche sur chaque poussée vers le cluster de
démonstration : voir la règle de coût plus bas.

## Câblage du déclenchement automatique (à faire, pas fait aujourd'hui)

Les workflows fonctionnent dès aujourd'hui en déclenchement manuel
(`workflow_dispatch`, onglet Actions). Pour qu'ils se déclenchent tout seuls à
chaque poussée sur `edumatch-ia`, il faut y ajouter un petit workflow qui
notifie celui-ci — GitHub n'exécute un workflow que sur les événements du
dépôt où il vit, donc un fichier doit exister côté edumatch-ia pour relayer
l'événement ici.

Contenu à créer dans `edumatch-ia/.github/workflows/notifier-cicd.yml`
(à créer côté edumatch-ia — ce dépôt-ci ne le modifie jamais) :

```yaml
name: Notifier edumatch-cicd
on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:
    branches: [main]

jobs:
  notifier:
    runs-on: ubuntu-latest
    steps:
      - name: Déterminer le type d'événement
        id: type
        run: |
          if [ "${{ startsWith(github.ref, 'refs/tags/') }}" = "true" ]; then
            echo "type=edumatch-ia-tag" >> "$GITHUB_OUTPUT"
          else
            echo "type=edumatch-ia-push" >> "$GITHUB_OUTPUT"
          fi
      - name: Notifier
        uses: peter-evans/repository-dispatch@v3
        with:
          token: ${{ secrets.EDUMATCH_CICD_PAT }}
          repository: ${{ github.repository_owner }}/edumatch-cicd
          event-type: ${{ steps.type.outputs.type }}
          client-payload: '{"sha": "${{ github.sha }}", "ref": "${{ github.ref }}"}'
```

Nécessite un secret `EDUMATCH_CICD_PAT` créé **côté edumatch-ia** (pas ici) :
un jeton d'accès à granularité fine, ciblant ce dépôt (`edumatch-cicd`),
permission « Contents : lecture et écriture » (c'est ce que l'API
`repository_dispatch` exige sur le dépôt cible). Tant que ce fichier n'existe
pas côté edumatch-ia, les trois workflows d'ici restent utilisables à la main.

## Secrets à créer — liste exacte

Tous se créent dans **ce dépôt** (`edumatch-cicd`), Settings → Secrets and
variables → Actions. Deux onglets distincts : **Secrets** (valeurs jamais
réaffichées) et **Variables** (valeurs non sensibles, lisibles dans les
journaux).

### Secrets

| Nom | Utilisé par | Valeur | Où l'obtenir |
|---|---|---|---|
| `EDUMATCH_IA_PAT` | `ci.yml`, `build-images.yml` | Jeton GitHub à granularité fine | github.com → Settings du compte → Developer settings → Fine-grained tokens → Generate new token. Repository access : uniquement `edumatch-ia`. Permissions : **Contents → Read-only**. Nécessaire seulement si `edumatch-ia` est un dépôt privé ; si le dépôt est public, ce secret peut rester vide (le workflow retombe alors sur `github.token`, qui ne suffit pas pour un dépôt privé tiers). |
| `SCW_SECRET_KEY` | `build-images.yml` (connexion au registre) | Clé secrète Scaleway | Console Scaleway → icône du compte → Identifiants API (API Keys) → Générer une nouvelle clé API. La clé secrète n'est affichée qu'une seule fois : la copier immédiatement dans ce secret. |
| `SCW_ACCESS_KEY` | réservé pour l'étape infrastructure (Terraform) | Clé d'accès Scaleway | Même écran que ci-dessus, affichée à côté de la clé secrète. Non utilisée par les workflows d'aujourd'hui, mais à créer maintenant : c'est la même paire de clés qui servira à Terraform. |
| `SCW_DEFAULT_PROJECT_ID` | réservé pour l'étape infrastructure | Identifiant du projet Scaleway | Console Scaleway → sélecteur de projet en haut de l'écran → Paramètres du projet → Project ID. |
| `SCW_KUBECONFIG_B64` | `deploy.yml` | Fichier de connexion au cluster Kapsule, encodé en base64 | Une fois le cluster de démonstration créé (étape infrastructure) : `scw k8s kubeconfig get <cluster-id> region=fr-par > kubeconfig.yaml`, puis `base64 -w0 kubeconfig.yaml` (Linux/macOS) ou `[Convert]::ToBase64String([IO.File]::ReadAllBytes("kubeconfig.yaml"))` (PowerShell), et coller le résultat tel quel. Ce secret n'est pas créé à ce jour : `deploy.yml` s'arrête proprement sans lui, et le déploiement du 22 septembre 2026 a été fait par `scripts/deploiement.py`, qui lit le kubeconfig installé localement. Sa création est planifiée avant l'exécution de `deploy.yml`. |

### Variables (non sensibles)

| Nom | Utilisé par | Exemple de valeur |
|---|---|---|
| `SCW_REGISTRY_ENDPOINT` | `build-images.yml`, `deploy.yml` | `rg.fr-par.scw.cloud/edumatch` — obtenu après création d'un namespace dans Console Scaleway → Registre de conteneurs → Créer un namespace (région `fr-par`, nom `edumatch`) ; l'écran affiche l'URL du registre à utiliser. |

## Ce qui reste à confirmer

La séquence d'infrastructure a été exécutée une première fois les 18 et 19 septembre 2026 :
`terraform apply`, cluster Kapsule `Ready` en v1.36.4, secrets Kubernetes créés, puis
destruction complète le 19 — coût mesuré sur la facturation, 1,00 €. `build-images.yml` a publié les trois images le 22 septembre 2026 ; `deploy.yml` reste à
exécuter, le déploiement ayant été fait par `scripts/deploiement.py`. Les deux points suivants
restent écrits d'après la documentation publique de Scaleway et de GitHub :

- **La connexion au registre** (`docker/login-action` dans `build-images.yml`)
  utilise la convention `utilisateur = nologin`, `mot de passe = clé secrète`.
  Si Scaleway a changé cette convention, l'erreur apparaîtra clairement à
  l'étape « Se connecter au registre Scaleway » et sera aisée à corriger dans
  ce seul fichier.
- **Les permissions exactes requises par `EDUMATCH_CICD_PAT`** pour
  `repository_dispatch` (Contents : lecture et écriture) sont celles
  documentées par GitHub au moment de l'écriture ; à reconfirmer si l'appel
  échoue avec une erreur 403.

## Règle de coût — pourquoi rien ne se déclenche sur chaque poussée vers le cluster

Le cluster Kapsule n'est provisionné que pour les démonstrations : le budget
de ce projet est celui d'une startup en amorçage, pas celui d'une
infrastructure permanente. `deploy.yml` ne se déclenche donc jamais tout seul
sur une poussée ordinaire — seulement à la main ou sur une étiquette de
version, un geste délibéré. `build-images.yml` construit à chaque poussée sur
`main` (coût faible : quelques minutes de calcul et un stockage d'image
léger), mais ne déploie rien.

## Utilisation en local avant la démonstration

Rien ici ne remplace `docker-compose.yml` d'edumatch-ia pour le développement
courant : ces workflows ciblent la démonstration en production, pas le poste
de développement. Pour lancer un workflow à la main : onglet **Actions** de ce
dépôt → sélectionner le workflow → **Run workflow** → renseigner la référence
(branche, tag ou commit d'edumatch-ia) demandée.

## Prochaines étapes (hors périmètre de ce qui est livré ici)

1. ~~Infrastructure : Terraform (registre, cluster Kapsule, état distant
   verrouillé)~~ — fait, voir `terraform/`.
2. ~~Manifestes Kubernetes (`Deployment`, `Service`, `ConfigMap`,
   `HorizontalPodAutoscaler`, `requests`/`limits` chiffrés)~~ — fait, voir
   `k8s/base/`. `deploy.yml` est réellement exécutable dès que le cluster
   existe et que les secrets Kubernetes (`k8s/secret.example.yaml`) ont été
   créés à la main.
3. ~~Monitoring (Prometheus, Alertmanager, Grafana, alertes actionnables)~~ :
   écrit, voir `monitoring/`. L'instrumentation de l'API est en place côté
   `edumatch-ia` (`/metrics`, deux métriques métier). Le SLO de latence est
   **déclaré** à p95 sous 300 ms, et il n'est **pas tenu** : le banc
   d'edumatch-ia mesure 844 ms sur le plus gros département (voir
   `monitoring/slo.md`). Les manifestes de supervision ont été appliqués sur
   le cluster le 25 septembre 2026 : Prometheus, Alertmanager et Grafana en
   `1/1 Running`, les deux réplicas d'`edumatch-serve` découverts et en
   `health: up`, et les cinq règles d'alerte chargées dans le groupe
   `edumatch-serve.symptomes`. Alertmanager n'a aucun destinataire externe :
   les alertes sont visibles, elles ne notifient personne.
4. ~~Instance dédiée à Airflow (ADR 0019) : `terraform/airflow.tf`,
   `terraform/cloud-init/airflow.yaml`, image `edumatch-airflow` dans
   `build-images.yml`~~ — fait, voir `terraform/README.md`, section
   "L'instance Airflow". Reste bloquant côté `edumatch-ia`, listé dans
   l'ADR 0019 lui-même : `docker-compose.prod.yml` et les scripts de
   démonstration de panne. La correction de `docker/Dockerfile.airflow` est
   faite, sans Java ni `[spark]` (ADR 0019, section « Amendement du
   2026-09-15 »).
5. Câblage du déclenchement automatique (section ci-dessus).
6. Panne provoquée et reprise, filmée — une fois le cluster et l'API
   effectivement déployés au moins une fois, avec le monitoring en place
   pour observer l'alerte se déclencher puis se résorber. Pour le pipeline,
   la panne se filme sur l'instance Airflow (voir l'ADR 0019, section
   "Comment la panne sera montrée dans cet environnement"), pas sur le
   cluster Kapsule.

## Infrastructure de démonstration — le script d'automatisation

Le déploiement se pilote désormais par un seul point d'entrée,
`scripts/deploiement.py` (bibliothèque standard Python 3.11, aucune
dépendance à installer) : voir `scripts/README.md` pour la séquence en cinq
lignes et le détail de chaque sous-commande. Il exécute ce que la séquence
manuelle ci-dessous décrit en prose, avec les mêmes garde-fous — jamais
d'application d'un plan non lu, jamais de destruction sans confirmation,
aucun secret journalisé.

```bash
cp scaleway.env.example scaleway.env   # puis y coller les vraies valeurs (jamais commité)
python scripts/deploiement.py tout
python scripts/deploiement.py appliquer     # après avoir lu le plan
python scripts/deploiement.py kubeconfig && python scripts/deploiement.py secrets-k8s
python scripts/deploiement.py deployer --image-tag <empreinte-commit> && python scripts/deploiement.py monitoring
```

La séquence manuelle ci-dessous reste la référence détaillée — ce que le
script exécute concrètement, commande par commande, utile pour comprendre
ou dépanner une étape précise.

### Ce que cette séquence a déjà produit

Exécutée une première fois les 18 et 19 septembre 2026, jusqu'à la création des secrets
Kubernetes : cinq ressources créées, cluster Kapsule `Ready` en v1.36.4, puis destruction
complète le 19 pour un coût mesuré de 1,00 €. Les manifestes applicatifs de `k8s/base/` ont été
appliqués sur le cluster le 22 septembre 2026, avec l'image `1acab97`. La supervision a été
déployée le 25 septembre 2026 par `python scripts/deploiement.py monitoring` : trois pods
`1/1 Running` dans l'espace de noms `monitoring`, la cible `edumatch-serve` découverte avec ses
deux réplicas en `health: up`, et les cinq règles d'alerte chargées. La séquence est à exécuter
dans cet ordre exact, en lisant
chaque sortie avant de continuer (jamais un `plan`/`apply` enchaînés sans
relecture).

### 0. Préalables, une seule fois

1. Compte Scaleway créé, projet créé, clé API générée (Console → icône du
   compte → Identifiants API) : donne `SCW_ACCESS_KEY`, `SCW_SECRET_KEY`,
   `SCW_DEFAULT_PROJECT_ID` — les mêmes valeurs que les secrets GitHub
   Actions déjà créés à l'étape CI/CD (voir plus haut dans ce document).
2. Les exporter dans le terminal qui exécutera Terraform (jamais dans un
   fichier versionné) :
   ```
   export SCW_ACCESS_KEY=...
   export SCW_SECRET_KEY=...
   export SCW_DEFAULT_PROJECT_ID=...
   export AWS_ACCESS_KEY_ID=$SCW_ACCESS_KEY        # réutilisées par le backend Terraform
   export AWS_SECRET_ACCESS_KEY=$SCW_SECRET_KEY    # (voir terraform/versions.tf)
   ```
3. Créer le bucket d'état Terraform (amorçage, ne se fait qu'une fois — voir
   `terraform/README.md`, section "Amorçage") : Console Scaleway → Object
   Storage → Créer un bucket → `edumatch-tfstate`, région `fr-par`, privé.

### 1. Provisionner l'infrastructure (Terraform)

```
cd terraform
terraform init
terraform validate
terraform plan -out=plan.tfout        # LIRE CE PLAN avant de continuer — jamais d'apply sans le lire
terraform apply plan.tfout
terraform output                      # note registre_endpoint et bucket_artefacts
```

### 2. Préparer les artefacts et l'accès au cluster

```
# Récupérer les identifiants du cluster créé
scw k8s kubeconfig install $(terraform output -raw cluster_id) region=fr-par
kubectl get nodes                     # doit lister 1 nœud DEV1-M

# Déposer les artefacts précalculés (produits par `make train` puis
# `make explain`, `make departements` côté edumatch-ia) dans le bucket — remplace le volume
# partagé que l'API n'a pas en démonstration (voir terraform/main.tf)
aws --endpoint-url https://s3.fr-par.scw.cloud s3 cp \
    edumatch-ia/data/processed/matching/catalogue_predictions.parquet \
    s3://edumatch-artefacts/matching/catalogue_predictions.parquet
# Libellés des départements de la liste déroulante de l'écran (`make
# departements` côté edumatch-ia) : sans ce fichier, la liste affiche les
# codes seuls.
aws --endpoint-url https://s3.fr-par.scw.cloud s3 cp     edumatch-ia/data/processed/matching/departements_libelles.parquet     s3://edumatch-artefacts/matching/departements_libelles.parquet
aws --endpoint-url https://s3.fr-par.scw.cloud s3 cp \
    edumatch-ia/data/processed/explicabilite/explications_locales.parquet \
    s3://edumatch-artefacts/explicabilite/explications_locales.parquet

# Créer les secrets Kubernetes (jamais commités) — voir le gabarit exact
# et les commandes dans k8s/secret.example.yaml
kubectl create namespace edumatch
kubectl create secret generic edumatch-object-storage --namespace edumatch \
    --from-literal=access-key-id=$SCW_ACCESS_KEY \
    --from-literal=secret-access-key=$SCW_SECRET_KEY
kubectl create secret docker-registry edumatch-registry-pull --namespace edumatch \
    --docker-server=$(cd ../terraform && terraform output -raw registre_endpoint) \
    --docker-username=nologin \
    --docker-password=$SCW_SECRET_KEY
# Comptes nominatifs de l'écran conseiller : sans eux, /matching refuse
# tout. Empreinte calculée hors ligne depuis edumatch-ia avec
# `python -m edumatch.api.auth "<mot de passe>"` (voir k8s/README.md)
kubectl create secret generic edumatch-conseillers --namespace edumatch     --from-literal=comptes='<identifiant>:<empreinte>'
```

### 3. Publier une image et déployer

```
# Depuis l'onglet Actions de ce dépôt (edumatch-cicd) :
# 1. build-images.yml → Run workflow → référence edumatch-ia à construire (ex. main)
# 2. deploy.yml → Run workflow → git_sha = l'empreinte affichée par build-images.yml, environnement = demo
```

### 4. Vérifier

```
kubectl get pods -n edumatch -w                           # les 2 réplicas (plancher de disponibilité) passent Running puis Ready
kubectl get hpa -n edumatch                                # cible CPU visible, réplicas actuels/min(2)/max(6)
kubectl port-forward -n edumatch svc/edumatch-serve 8000:80
curl http://localhost:8000/health
```
Ouvrir `http://localhost:8000/` dans un navigateur pour l'écran conseiller
(l'écran de supervision du conseiller), ou appeler `/matching` directement — c'est le moment de filmer la
vidéo de production exigée par les blocs 2 et 4.

### 4bis. Instance Airflow (ADR 0019) — séquence propre, indépendante du cluster

L'instance Airflow ne partage rien avec le cluster Kapsule sinon le réseau
privé : elle se provisionne, se tourne et se détruit séparément, avec sa
propre bascule de coût (`airflow_active`). Séquence détaillée et coût dans
`terraform/README.md`, section "L'instance Airflow (ADR 0019)" ; résumé :

```
cd terraform
curl -4 ifconfig.me                        # note l'IP à mettre dans cidr_operateur
# terraform.tfvars : cidr_operateur = "<ip-notée>/32", airflow_active = true
terraform plan -out=plan-airflow.tfout     # LIRE CE PLAN
terraform apply plan-airflow.tfout
$(terraform output -raw airflow_commande_tunnel)   # ouvre le tunnel SSH
# dans une seconde fenêtre : http://localhost:8080 pour l'interface Airflow
```

Puis, une fois le tournage terminé :

```
# terraform.tfvars : airflow_active = false
terraform plan -out=destroy-airflow.tfout  # LIRE CE PLAN aussi
terraform apply destroy-airflow.tfout
```

### 5. Détruire — toujours après la démonstration

```
kubectl delete namespace edumatch      # retire l'API et ses objets, garde le cluster
cd terraform
terraform plan -destroy -out=destroy.tfout   # LIRE CE PLAN aussi
terraform apply destroy.tfout
```
`terraform destroy` (ou le `apply` d'un plan `-destroy`) suffit : le cluster,
le pool, le réseau privé, le registre et le bucket d'artefacts sont tous
gérés par ce Terraform (`delete_additional_resources = true` sur le cluster
retire aussi ce qu'il a lui-même créé, comme un volume de nœud). Rien ne doit
rester après cette commande — un cluster oublié tourne pour rien, exactement
l'erreur d'exploitation que la règle de coût interdit.

### Coût de la séquence complète

Mesuré sur la facturation Scaleway : 1,00 EUR pour les 34,9 heures de la
démonstration du 18 au 19 septembre 2026 (détail par poste dans
`terraform/README.md`) — à condition de détruire à l'étape 5. Un cluster
laissé actif coûte environ 20 EUR pour un mois oublié avec un nœud, 40 EUR
avec deux, pour un
budget de projet qui est celui d'une startup en amorçage : c'est moi qui
paie, pas un budget d'entreprise.
