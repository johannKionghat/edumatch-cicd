# Versions épinglées — jamais de borne ouverte (`>=` seul), c'est la règle de
# sécurité que je me suis fixée pour les deux dépôts : une borne ouverte laisse
# un `terraform init` futur résoudre une version différente sans qu'aucune ligne
# de ce dépôt n'ait changé, ce qui casse la reproductibilité.
#
# Version du provider vérifiée le 2026-09-15 via l'API des releases GitHub du
# projet `scaleway/terraform-provider-scaleway` (dernière étiquette :
# v2.83.0). Je n'ai pas pu exécuter `terraform init` contre cette version
# faute de compte Scaleway et de binaire Terraform dans cet environnement —
# voir le README pour ce que ça implique.
terraform {
  required_version = "= 1.10.5"

  required_providers {
    scaleway = {
      source  = "scaleway/scaleway"
      version = "= 2.83.0"
    }
  }

  # État distant — jamais un fichier local versionné ou non. Un état local
  # ne pose pas de problème pour une seule personne qui n'exécute jamais deux
  # `apply` en parallèle ; il en pose un dès qu'un deuxième poste (ou un
  # second job CI) peut lancer `apply` en même temps : les deux liraient le
  # même état de départ, et le second écraserait le travail du premier sans
  # avertissement. Le bucket cible (`edumatch-tfstate`, compatible S3 chez
  # Scaleway) n'est PAS créé par ce Terraform : un backend ne peut pas
  # s'auto-provisionner, il doit exister avant le premier `terraform init`
  # (voir README, section "Amorçage").
  #
  # `use_lockfile = true` (natif depuis Terraform 1.10, backend S3) pose un
  # fichier de verrou `.tflock` à côté de l'état dans le même bucket : deux
  # `apply` concurrents se bloquent l'un l'autre au lieu de corrompre l'état.
  # Alternative écartée : une table DynamoDB de verrouillage, le mécanisme
  # historique du backend S3 — elle n'a pas d'équivalent direct chez
  # Scaleway (pas de service compatible DynamoDB), et `use_lockfile`
  # supprime ce besoin pour un coût d'exploitation nul.
  backend "s3" {
    bucket = "edumatch-tfstate"
    key    = "edumatch-cicd/terraform.tfstate"
    region = "fr-par"

    endpoints = {
      s3 = "https://s3.fr-par.scw.cloud"
    }

    # Le backend S3 de Terraform suppose par défaut un compte AWS réel :
    # ces trois indicateurs désactivent les vérifications propres à AWS
    # (validation des identifiants IAM, de la région AWS, appel à STS pour
    # l'identifiant de compte) qui n'ont pas de sens contre un point de
    # terminaison Scaleway compatible S3.
    skip_credentials_validation = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
    use_path_style              = true
    use_lockfile                = true

    # Identifiants lus depuis l'environnement (AWS_ACCESS_KEY_ID /
    # AWS_SECRET_ACCESS_KEY, mêmes valeurs que les clés API Scaleway — voir
    # README) : jamais en dur dans ce fichier, jamais dans un `.tfvars`.
  }
}

# Non vérifié dans cette session : le comportement exact de `use_lockfile`
# et de `use_path_style` contre le point de terminaison Object Storage de
# Scaleway. Documenté d'après la note de version Terraform 1.10 et la
# documentation Scaleway sur la compatibilité S3, pas exécuté ici faute
# d'identifiants réels. Si `terraform init` échoue sur le backend, c'est le
# premier fichier à relire.
