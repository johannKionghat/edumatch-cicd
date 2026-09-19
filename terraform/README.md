# Infrastructure as code (Terraform)

Provisionne le socle Scaleway du cluster de démonstration : réseau privé,
cluster Kubernetes (Kapsule), pool de nœuds, registre de conteneurs, bucket
d'artefacts. Ce que ce socle héberge (Deployment, Service, HPA...) vit dans
`k8s/`, appliqué par `kubectl` via `deploy.yml`, pas par ce Terraform — voir
la note de périmètre à la fin de `main.tf`.

## Pourquoi Scaleway plutôt qu'un des trois grands fournisseurs

Deux raisons, une juridique et une de coût :

- **Souveraineté.** Le projet traite des données de mineurs (candidats
  Parcoursup, dont une partie sont mineurs au moment du vœu). AWS, Azure et
  GCP sont des sociétés de droit américain soumises au *CLOUD Act*, qui peut
  contraindre une filiale européenne à transmettre des données hébergées en
  Europe à une autorité américaine, indépendamment du RGPD. Scaleway est une
  société de droit français, sans cette exposition. Ce point est développé
  dans l'analyse d'impact (AIPD) côté `edumatch-ia`.
- **Coût du plan de contrôle Kubernetes.** Le plan de contrôle Kapsule
  mutualisé (plusieurs clients sur les mêmes machines de contrôle) est sans
  frais chez Scaleway ; EKS (AWS) facture le plan de contrôle environ
  73 USD/mois quel que soit l'usage. Pour un cluster qui ne tourne que
  pendant les démonstrations, ce delta n'est pas marginal : c'est la
  différence entre un coût proportionnel au temps d'usage et un forfait fixe.

Alternative écartée : rester en développement local uniquement, sans jamais
provisionner de cluster. Elle prouverait moins bien le critère 2.3
(infrastructure *déployée*) et empêcherait la vidéo de production exigée par
plusieurs blocs. Le seuil qui ferait reconsidérer Scaleway : un besoin de
service managé absent de son catalogue (un service IA propriétaire, par
exemple) qu'aucun des trois grands ne serait seul à proposer — pas le cas ici.

## Décisions déjà arrêtées, maintenant appliquées

- **Immuable et déclaratif** : on remplace des ressources plutôt qu'on ne les
  modifie en place (`scaleway_k8s_pool` recrée un pool plutôt que de migrer
  ses nœuds un par un) ; on décrit l'état voulu, jamais une séquence de
  commandes.
- **`terraform plan` toujours lu avant `apply`**, y compris en démonstration —
  aucun `apply` automatique sans relecture humaine du plan tant que ce projet
  est porté par une seule personne.
- **État distant et verrouillé** dès la première exécution : bucket Scaleway
  Object Storage (`edumatch-tfstate`) comme backend `s3`, verrouillage natif
  par fichier (`use_lockfile`, Terraform ≥ 1.10) plutôt qu'une table de
  verrouillage façon DynamoDB, qui n'a pas d'équivalent direct chez Scaleway.
  Deux `apply` concurrents sur un état local corrompraient l'infrastructure :
  chacun partirait du même état de départ et le second écraserait le travail
  du premier sans avertissement.
- **Le cluster n'est provisionné que pour les démonstrations.** Le
  développement courant se fait en local via `docker-compose.yml`
  (edumatch-ia). Une infrastructure Kapsule oubliée en fonctionnement est une
  erreur d'exploitation : `terraform destroy` est exécuté après chaque
  démonstration, pas laissé tourner par prudence.

## Ce que chaque fichier fait

| Fichier | Contenu |
|---|---|
| `versions.tf` | Version de Terraform et du provider épinglées, backend `s3` distant |
| `variables.tf` | Paramètres non sensibles (région, taille de nœud, bornes du pool, instance Airflow...) |
| `main.tf` | Réseau privé, cluster Kapsule, pool, registre, bucket d'artefacts |
| `airflow.tf` | Instance dédiée à l'orchestrateur Airflow (ADR 0019) : IP publique, groupe de sécurité, volume de données, instance |
| `cloud-init/airflow.yaml` | Initialisation de l'instance Airflow au premier démarrage : Docker, arborescence de données, aucun secret |
| `outputs.tf` | Identifiants utiles après `apply` — rien de sensible |
| `terraform.tfvars.example` | Valeurs par défaut documentées, à copier en `terraform.tfvars` si besoin |

## L'instance Airflow (ADR 0019)

Le cluster Kapsule héberge l'API, dont la charge suit la campagne de vœux
(rapport de 1 à 6, absorbé par le HPA). Le pipeline de données suit une
cadence différente — annuelle, mensuelle, quotidienne selon le DAG — et n'a
aucun pic concurrent à absorber : il n'a pas besoin d'un cluster élastique.
La décision complète, avec les chiffres de mémoire qui l'ont emportée sur
« Airflow sur Kapsule », est dans l'ADR 0019, côté edumatch-ia (`docs/decisions.html`).

Toutes les ressources de `airflow.tf` sont conditionnées par
`var.airflow_active` (`false` par défaut) : l'instance n'existe, et ne se
facture, que pendant les séances de tournage — exactement la même
discipline que pour le cluster Kapsule, appliquée ici à une machine
facturée à l'heure plutôt qu'à un pool élastique.

### Séquence propre à l'instance Airflow

```bash
cd terraform

# 1. Trouver sa propre adresse IP publique, pour cidr_operateur
curl -4 ifconfig.me
# noter le résultat, l'écrire dans terraform.tfvars sous la forme
# cidr_operateur = "203.0.113.42/32"

# 2. Activer l'instance et relire le plan avant d'appliquer
#    (dans terraform.tfvars : airflow_active = true)
terraform plan -out=plan-airflow.tfout     # LIRE CE PLAN
terraform apply plan-airflow.tfout
terraform output airflow_ip_publique
terraform output airflow_commande_tunnel

# 3. Se connecter et déployer la pile applicative
#    (côté edumatch-ia : docker-compose.prod.yml, voir son README)
$(terraform output -raw airflow_commande_tunnel)
# puis, dans une seconde fenêtre, ouvrir http://localhost:8080 dans un
# navigateur pour l'interface Airflow — jamais exposée autrement

# 4. Tourner, filmer la panne et la reprise (voir l'ADR 0019, section
#    "Comment la panne sera montrée dans cet environnement")

# 5. Détruire — toujours après la séance
#    (dans terraform.tfvars : airflow_active = false)
terraform plan -out=destroy-airflow.tfout   # LIRE CE PLAN aussi
terraform apply destroy-airflow.tfout
```

### Coût de l'instance Airflow, ordre de grandeur

L'instance n'a jamais été créée : aucun de ces chiffres n'est une mesure.

| Poste | Prix | Source |
|---|---|---|
| Instance DEV1-L (4 vCPU, 8 Go) | 0,04284 EUR/heure, 31,27 EUR/mois en continu | API Scaleway (`scw instance server-type list`), 2026-09-19 |
| IPv4 flexible | 0,005 EUR/heure, 3,65 EUR/mois | facture Scaleway du nœud Kapsule, même tarif |
| Volume bloc 60 Go (5K) | ≈ 5,70 EUR/mois si conservé un mois complet | page tarifaire publique, 2026-09-15 |

Pour une séance de tournage de 6 heures : de l'ordre de **0,35 EUR**. Une
instance oubliée active pendant un mois entier coûterait de l'ordre de
**40 EUR** pour rien : c'est exactement l'erreur d'exploitation que la règle
de coût interdit, et `airflow_active = false` est le geste qui l'évite.

## Amorçage — à faire une seule fois, avant le premier `terraform init`

Un backend distant ne peut pas se créer lui-même : le bucket `edumatch-tfstate`
doit exister *avant* que Terraform puisse s'en servir pour stocker son état.
Deux façons de le créer, une seule à faire :

- **Console Scaleway** → Object Storage → Créer un bucket → nom
  `edumatch-tfstate`, région `fr-par`, visibilité privée.
- **Ligne de commande**, si le CLI `scw` est installé et configuré :
  `scw object bucket create name=edumatch-tfstate region=fr-par`

## Ce qui n'a pas pu être vérifié ici

Je n'ai ni compte Scaleway ni binaire Terraform dans cet environnement. Rien
n'a été exécuté au-delà d'une relecture manuelle ligne par ligne. Points
précis à reconfirmer avant le premier `apply`, chacun isolé dans un seul
fichier pour que la correction reste rapide :

- **Le comportement du backend `s3` contre le point de terminaison Object
  Storage de Scaleway** (`use_lockfile`, `use_path_style`) — voir la note en
  tête de `versions.tf`.
- **L'argument `acl` porté directement par `scaleway_object_bucket`** plutôt
  que par une ressource séparée dans la version 2.83.0 du provider — voir la
  note dans `main.tf`, à côté de la ressource `artefacts`.
- **Les versions de Kubernetes réellement proposées par Kapsule au moment de
  la démonstration** (`version_kubernetes` dans `variables.tf`, valeur
  `1.30` non confirmée contre `scw k8s version list`).
- **Un conflit possible avec le namespace de registre `edumatch`** créé à la
  main pendant l'étape CI/CD précédente (voir la note dans `main.tf`, section
  "Registre de conteneurs") — à trancher (import ou recréation) avant le
  premier `apply`.
- **Le libellé exact de l'image Ubuntu LTS** utilisée par
  `scaleway_instance_server.airflow` (`airflow.tf`) : `ubuntu_jammy` suit la
  convention observée dans la documentation publique Scaleway au
  2026-09-15, mais elle évolue avec les images retirées ou ajoutées au
  catalogue — à confirmer par `scw instance image list zone=fr-par-1` avant
  le premier `apply`.
- **Les noms d'attributs `ip_id` et le bloc `private_network` sur
  `scaleway_instance_server`**, ainsi que `stateful` sur
  `scaleway_instance_security_group`, dans la version 2.83.0 du provider —
  non exécutés faute de binaire Terraform à ce jour, à confirmer
  par `terraform validate`.

`terraform fmt` a été appliqué manuellement (indentation à deux espaces,
alignement des `=`) aux fichiers existant avant l'ajout d'`airflow.tf` ;
faute de binaire Terraform à ce jour, ni `terraform fmt` ni
`terraform validate` n'ont pu être exécutés sur `airflow.tf` et
`cloud-init/airflow.yaml`. Les deux commandes sont à lancer avant `plan`, dès
que Terraform est disponible sur le poste qui exécutera réellement le
provisionnement :

```bash
terraform fmt -recursive
terraform validate
```

## Coût — mesuré sur la facturation

Mesuré le 19 septembre 2026 par `scw billing consumption list`, relu après
la destruction du cluster, qui a vécu 34,9 heures (du 18 septembre, 3 h 41
UTC, au 19 septembre, 14 h 37 UTC). Projection en continu sur 730 heures.

| Poste | Prix horaire | Coût démo mesuré | Continu projeté / mois |
|---|---|---|---|
| Plan de contrôle Kapsule mutualisé | 0 EUR | 0,00 EUR | 0 EUR |
| Nœud DEV1-M (3 vCPU, 4 Go) | 0,020196 EUR (API) | 0,73 EUR | 14,74 EUR par nœud |
| IPv4 du nœud | 0,005 EUR | 0,18 EUR | 3,65 EUR par nœud |
| SSD local 40 Go du nœud | 0,00216 EUR (tiré de la facture) | 0,08 EUR | 1,58 EUR par nœud |
| Registre (vide) | 0 EUR | 0,00 EUR | ≈ 0 EUR |
| Buckets (artefacts, état Terraform) | 0,000286 EUR (tiré de la facture) | 0,01 EUR | ≤ 0,21 EUR |
| **Total** | **0,0277 EUR** | **1,00 EUR** | **20,18 EUR avec 1 nœud, 40,15 EUR avec 2** |

Le SSD local du nœud est facturé à part : le catalogue le présentait comme
inclus dans l'instance, la facture le montre en ligne distincte.

Une séance de démonstration coûte donc de l'ordre de l'euro, à condition de
détruire le cluster ensuite. Un cluster oublié coûte **environ 4,65 EUR par
semaine**, **20 EUR par mois** avec un nœud : c'est exactement l'erreur
d'exploitation que la règle de coût interdit. Le seul poste permanent est le
bucket d'état, créé hors de ce Terraform et conservé entre les séances.

## Séquence de commandes — voir le README à la racine du dépôt

La liste ordonnée exacte (provisionner, déployer, vérifier, détruire) est
rassemblée dans un seul endroit pour éviter deux versions divergentes : le
`README.md` à la racine d'`edumatch-cicd`.
