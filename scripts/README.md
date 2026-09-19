# `scripts/deploiement.py` — point d'entrée du déploiement Scaleway

Un seul script, bibliothèque standard Python 3.11 uniquement (aucune
dépendance à installer), qui automatise la séquence jusqu'ici décrite en
prose dans le `README.md` racine et `terraform/README.md`. Il n'invente
rien : il exécute, dans l'ordre, exactement ce que ces deux documents
demandaient déjà de taper à la main.

## La séquence complète, en cinq lignes

```bash
cp scaleway.env.example scaleway.env   # puis y coller les vraies valeurs (jamais commité)
python scripts/deploiement.py tout                              # vérifie, amorce le bucket d'état, produit un plan — LIRE le plan
python scripts/deploiement.py appliquer                         # après lecture du plan — crée le cluster, le registre, le bucket
python scripts/deploiement.py kubeconfig && python scripts/deploiement.py secrets-k8s
python scripts/deploiement.py deployer --image-tag <empreinte-commit> && python scripts/deploiement.py monitoring
```

Destruction, toujours après la démonstration : `python scripts/deploiement.py detruire`.

## Ce que fait chaque sous-commande

| Sous-commande | Ce qu'elle fait | Ce qu'elle refuse |
|---|---|---|
| `verifier` | Contrôle la présence et la version de `terraform` (exactement 1.10.5), `scw`, `kubectl`, `docker` ; contrôle la présence des cinq variables d'environnement nécessaires. Tableau lisible, aucune valeur de secret affichée. | Rien à refuser — c'est un diagnostic. |
| `amorcer` | Crée le bucket d'état Terraform `edumatch-tfstate` (région `fr-par`, privé) via `scw`, s'il n'existe pas déjà. | Recréer un bucket déjà présent (idempotent, le dit). |
| `plan` | `terraform init` (si nécessaire), `fmt -check`, `validate`, puis `plan -out=<fichier>`. | Rien n'est appliqué à ce stade. |
| `appliquer` | Applique un plan **existant et plus récent que tous les fichiers `.tf`**, après confirmation interactive (`--oui` pour l'automatiser). Affiche `cluster_id`, `registre_endpoint`, `bucket_artefacts` en sortie. | S'exécuter sans plan, avec un plan périmé, ou sans confirmation. |
| `kubeconfig` | Récupère et installe les identifiants du cluster (`scw k8s kubeconfig install`), vérifie l'accès (`kubectl get nodes`), affiche la commande — sans l'exécuter ni afficher son résultat — qui produit la valeur encodée attendue par `SCW_KUBECONFIG_B64`. | Afficher la valeur encodée elle-même. |
| `secrets-k8s` | Crée le namespace `edumatch` et les deux secrets Kubernetes (accès au stockage objet, accès au registre) à partir des variables d'environnement, via `--dry-run=client -o yaml \| kubectl apply -f -`. | Écrire une valeur en dur, journaliser le manifeste généré (qui contient les secrets encodés en base64). |
| `deployer` | Substitue `__EDUMATCH_SERVE_IMAGE__` par `<registre>/edumatch-serve:<tag>` dans `k8s/base/`, affiche `kubectl diff`, applique, attend la fin du déploiement (`kubectl rollout status`). | Déployer le tag `latest`. |
| `monitoring` | Applique les manifestes de `monitoring/` dans l'ordre de `monitoring/README.md` (Prometheus, Alertmanager, Grafana), crée le secret d'administration Grafana avec un mot de passe aléatoire (dit où le relire, ne l'affiche jamais), charge le tableau de bord. | Écraser un secret Grafana déjà créé (idempotent). |
| `detruire` | Supprime les namespaces `edumatch` et `monitoring`, puis `terraform plan -destroy` suivi de `apply`, avec confirmation explicite. Rappelle la règle de coût. | Détruire sans confirmation (sauf `--oui`), ou détruire sans avoir d'abord produit le plan de destruction. |
| `tout` | Enchaîne `verifier`, `amorcer`, `plan`. | S'arrête toujours avant `apply` — aucune application automatique du plan. |

## Garde-fous communs à toutes les commandes

- Chaque commande externe est affichée avant d'être lancée, secrets masqués.
- Le script s'arrête au premier échec, avec un message qui dit quoi
  vérifier ensuite (jamais un simple code de retour brut).
- Aucun secret n'est journalisé : les valeurs de `SCW_SECRET_KEY` et
  `AWS_SECRET_ACCESS_KEY` ne sont jamais affichées, même partiellement ;
  les identifiants publics (`SCW_ACCESS_KEY`, `SCW_DEFAULT_PROJECT_ID`,
  `AWS_ACCESS_KEY_ID`) ne sont montrés que sur leurs six premiers
  caractères, dans `verifier` uniquement.
- `terraform apply` et `terraform destroy` exigent tous deux un plan
  préexistant, plus récent que le code, et une confirmation explicite.

## Ce qui reste manuel, et pourquoi

- **Créer le compte Scaleway, le projet, la clé API** : geste unique, fait
  une fois dans la console, hors de portée d'un script qui n'a pas
  d'identifiants avant cette étape.
- **Lire le plan Terraform avant d'appliquer** : c'est le point que ce
  script refuse justement d'automatiser (voir `rules` du projet — un
  `apply` sans relecture humaine du plan n'est jamais acceptable).
- **Créer le secret GitHub Actions `SCW_KUBECONFIG_B64`** : la commande qui
  produit la valeur est affichée par `kubeconfig`, mais coller le résultat
  dans l'interface GitHub reste un geste humain — automatiser l'écriture
  d'un secret dans un dépôt tiers depuis ce script en ferait une nouvelle
  surface à sécuriser pour un gain marginal sur une opération faite une
  fois par rotation.
- **La panne provoquée et sa reprise, filmées** : ce script permet de
  reproduire le déploiement à l'identique avant l'exercice, il ne remplace
  pas l'exercice lui-même.
