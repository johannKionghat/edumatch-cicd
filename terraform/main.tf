# Provider Scaleway : project_id, access_key, secret_key ne sont PAS déclarés
# ici. Le provider les lit nativement depuis les variables d'environnement
# SCW_DEFAULT_PROJECT_ID, SCW_ACCESS_KEY, SCW_SECRET_KEY — les mêmes noms que
# les secrets GitHub Actions déjà créés à l'étape CI/CD (voir le README à la
# racine du dépôt). Les redéclarer comme variables Terraform les ferait
# passer par un `.tfvars`, un fichier qu'il faudrait alors exclure du
# versionnement avec une vigilance supplémentaire — autant ne jamais leur
# donner une forme qui pourrait finir sur disque en clair.
provider "scaleway" {
  region = var.region
  zone   = var.zone
}

# ─── Réseau : cloisonnement (critère 2.7) ───────────────────────────────
#
# Réseau privé dédié au cluster : les nœuds Kapsule communiquent entre eux et
# avec les ressources qui y sont rattachées sur ce réseau, jamais sur le
# réseau public par défaut partagé avec d'autres ressources du projet
# Scaleway. C'est le premier niveau de cloisonnement, avant même les
# `NetworkPolicy` Kubernetes (k8s/base/networkpolicy.yaml, qui cloisonnent
# *entre pods*, à l'intérieur du cluster).
resource "scaleway_vpc_private_network" "edumatch" {
  name = "${var.nom_projet}-${var.environnement}"
  tags = ["edumatch", var.environnement]
}

# ─── Cluster Kubernetes (Kapsule) ───────────────────────────────────────
#
# `type` n'est pas renseigné : la valeur par défaut de la ressource est le
# plan de contrôle Kapsule mutualisé (plusieurs clients sur les mêmes
# machines de contrôle, sans frais), retenu explicitement plutôt qu'un plan
# de contrôle dédié (~25 EUR/mois minimum chez Scaleway au moment de la
# décision) : cette démonstration n'a besoin d'aucune garantie de latence ou
# d'isolation du plan de contrôle lui-même, seulement que les pods
# applicatifs tournent et répondent. Le seuil qui ferait reconsidérer ce
# choix : un SLA contractuel sur la disponibilité du plan de contrôle, ce qui
# n'existe pas pour un projet de certification.
resource "scaleway_k8s_cluster" "edumatch" {
  name                        = "${var.nom_projet}-${var.environnement}"
  version                     = var.version_kubernetes
  cni                         = "cilium"
  private_network_id          = scaleway_vpc_private_network.edumatch.id
  delete_additional_resources = true # un `destroy` supprime aussi les load balancers/volumes créés par le cluster lui-même, pas seulement ce que Terraform a créé explicitement — condition du "tout détruire proprement" exigé par la règle de coût

  tags = ["edumatch", var.environnement]

  autoscaler_config {
    disable_scale_down = false
    # Fenêtre courte : en démonstration, un nœud qui redevient inutile doit
    # se libérer vite, pas rester facturé "au cas où" pendant une heure.
    scale_down_delay_after_add = "5m"
  }
}

# Pool de nœuds : voir variables.tf pour le choix de DEV1-M et le
# dimensionnement min/max. `autoscaling = true` laisse Scaleway ajouter ou
# retirer des nœuds VM en réaction à la pression des pods (elle-même pilotée
# par le HPA applicatif) ; sans lui, `pool_taille_max` n'aurait aucun effet
# et il faudrait redimensionner le pool à la main pendant une démonstration.
resource "scaleway_k8s_pool" "demo" {
  cluster_id  = scaleway_k8s_cluster.edumatch.id
  name        = "demo"
  node_type   = var.type_noeud
  size        = var.pool_taille_min
  min_size    = var.pool_taille_min
  max_size    = var.pool_taille_max
  autoscaling = true
  autohealing = true # un nœud qui ne répond plus est remplacé sans intervention manuelle

  tags = ["edumatch", var.environnement]
}

# ─── Registre de conteneurs ──────────────────────────────────────────────
#
# Espace de noms privé : une image mal identifiée ne doit pas être
# accessible à qui trouve l'URL du registre. Les nœuds Kapsule s'y
# authentifient avec un secret `docker-registry` créé dans le cluster (voir
# README, section "Séquence à exécuter" — ce secret est un objet Kubernetes,
# pas une ressource Scaleway, donc hors du périmètre de ce Terraform, au même
# titre que les autres objets Kubernetes : voir la note de périmètre
# ci-dessous).
#
# Si un espace de noms `edumatch` existe déjà (créé à la main pendant
# l'étape CI/CD précédente, voir le README racine du dépôt, section "Ce qui
# n'a pas pu être vérifié ici" → variable SCW_REGISTRY_ENDPOINT), ce bloc
# entrerait en conflit avec lui : soit l'importer dans l'état Terraform
# (`terraform import scaleway_registry_namespace.edumatch <id>`), soit le
# supprimer côté console et laisser Terraform le recréer. Je ne peux pas
# trancher à la place du candidat sans savoir lequel des deux a été fait
# concrètement — décision à prendre avant le premier `apply`, voir README.
resource "scaleway_registry_namespace" "edumatch" {
  name      = var.nom_projet
  is_public = false
  region    = var.region
}

# ─── Object Storage : artefacts du modèle ────────────────────────────────
#
# Le catalogue de prédictions et le précalcul SHAP (E22, E25 côté
# edumatch-ia — quelques dizaines à ~100 Mo à eux deux) sont déposés ici
# après `make train` / `make explain`, puis récupérés par un conteneur
# d'initialisation au démarrage de chaque pod edumatch-serve (voir
# k8s/base/deployment.yaml). Alternative écartée : un volume bloc persistant
# partagé (PersistentVolumeClaim en accès multi-nœuds) — Scaleway Block
# Storage est en accès exclusif à un seul nœud (ReadWriteOnce), ce qui
# empêcherait plusieurs pods répartis sur plusieurs nœuds de le monter en
# même temps, exactement le cas que le HPA produit dès qu'il dépasse un
# réplica. Un objet par pod, retéléchargé à chaque démarrage, coûte quelques
# secondes de démarrage supplémentaires mais fonctionne quel que soit le
# nombre de réplicas.
resource "scaleway_object_bucket" "artefacts" {
  name   = "${var.nom_projet}-artefacts"
  region = var.region
  acl    = "private"
  tags = {
    projet        = "edumatch"
    environnement = var.environnement
  }
}

# Non vérifié dans cette session (pas de `terraform` ni de compte Scaleway
# disponibles ici) : que l'argument `acl` soit toujours porté directement par
# `scaleway_object_bucket` dans la version 2.83.0 du provider, plutôt que par
# une ressource séparée `scaleway_object_bucket_acl` (le schéma a bougé dans
# ce sens chez AWS, dont le provider Scaleway s'inspire). Si `terraform
# validate` rejette l'argument `acl` ici, c'est le seul endroit à corriger —
# voir la documentation du provider à la version épinglée dans versions.tf.

# ─── Ce que ce Terraform ne gère volontairement pas ──────────────────────
#
# Aucune ressource `kubernetes_*` (provider Kubernetes de Terraform) ici : le
# Deployment, le Service, le HPA et les objets Secret vivent dans k8s/base/
# et sont appliqués par `deploy.yml` (kubectl), pas par Terraform. Séparer
# les deux évite un piège classique : faire dépendre un `terraform apply` de
# la disponibilité de l'API Kubernetes du cluster qu'il vient tout juste de
# créer (un ordonnancement fragile, un `apply` qui échoue au milieu si le
# cluster met trente secondes de plus que d'habitude à répondre). Terraform
# provisionne le socle (le cluster, le réseau, le registre, le bucket) ;
# `kubectl`, via la CI, provisionne ce qui vit dedans. Le seuil qui ferait
# reconsidérer ce choix : plusieurs environnements Kubernetes à maintenir en
# parfaite cohérence, où gérer les deux avec le même outil réduirait la
# dérive entre eux — pas la situation d'un cluster de démonstration unique.
