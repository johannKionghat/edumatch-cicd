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
| `variables.tf` | Paramètres non sensibles (région, taille de nœud, bornes du pool...) |
| `main.tf` | Réseau privé, cluster Kapsule, pool, registre, bucket d'artefacts |
| `outputs.tf` | Identifiants utiles après `apply` — rien de sensible |
| `terraform.tfvars.example` | Valeurs par défaut documentées, à copier en `terraform.tfvars` si besoin |

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

`terraform fmt` a été appliqué à tous les fichiers. `terraform validate` n'a
pas pu être exécuté (binaire absent de cet environnement) : à lancer en
premier, avant `plan`, dès que Terraform est disponible sur le poste qui
exécutera réellement le provisionnement.

## Coût — ordre de grandeur, à vérifier avant de laisser tourner

| Poste | Coût constaté sur la page tarifaire publique Scaleway (2026-09-15) |
|---|---|
| Plan de contrôle Kapsule mutualisé | Sans frais |
| 1 nœud DEV1-M (3 vCPU, 4 Go) | ≈ 0,02 EUR/heure, ≈ 14,74 EUR/mois s'il tournait un mois complet |
| Bucket Object Storage (quelques centaines de Mo) | Négligeable (tarif au Go stocké, très en dessous du seuil facturé en pratique) |

Avec un pool à 1 nœud pendant la préparation et 2 pendant la démonstration
filmée, une session de quelques heures coûte de l'ordre de quelques dizaines
de centimes d'euro — mais seulement si le cluster est détruit ensuite. Un
cluster oublié une semaine coûte de l'ordre de 15 à 30 EUR pour rien : c'est
exactement l'erreur d'exploitation que la règle de coût interdit.

## Séquence de commandes — voir le README à la racine du dépôt

La liste ordonnée exacte (provisionner, déployer, vérifier, détruire) est
rassemblée dans un seul endroit pour éviter deux versions divergentes : le
`README.md` à la racine d'`edumatch-cicd`.
