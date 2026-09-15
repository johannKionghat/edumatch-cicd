# Aucun secret ici : project_id, access_key et secret_key sont lus par le
# provider directement depuis l'environnement (SCW_DEFAULT_PROJECT_ID,
# SCW_ACCESS_KEY, SCW_SECRET_KEY — les mêmes noms que les secrets déjà créés
# pour les workflows CI/CD, voir la racine du dépôt). Seuls les paramètres
# non sensibles, qui décrivent la forme de l'infrastructure, sont déclarés
# comme variables Terraform.

variable "region" {
  description = "Région Scaleway. fr-par (Paris) : aucune raison de sortir de France pour une démonstration, et c'est la région où le registre de conteneurs et les buckets sont déjà documentés côté CI/CD."
  type        = string
  default     = "fr-par"
}

variable "zone" {
  description = "Zone de disponibilité au sein de la région. Une seule zone suffit pour un cluster de démonstration à un ou deux nœuds : la tolérance multi-zone est un raffinement de production, pas un besoin ici."
  type        = string
  default     = "fr-par-1"
}

variable "nom_projet" {
  description = "Préfixe des noms de ressources (cluster, pool, namespace de registre, bucket)."
  type        = string
  default     = "edumatch"
}

variable "environnement" {
  description = "Étiquette portée par toutes les ressources (tag `environment`). Une seule valeur existe aujourd'hui : la démonstration. Pas de `staging` ni de `prod` Kubernetes tant qu'aucune charge réelle ne les justifie (règle de coût du projet)."
  type        = string
  default     = "demo"

  validation {
    condition     = contains(["demo"], var.environnement)
    error_message = "Un seul environnement Kubernetes existe pour l'instant : demo. Voir terraform/README.md avant d'en ajouter un second — le coût double à chaque environnement permanent."
  }
}

variable "version_kubernetes" {
  description = "Version mineure de Kubernetes demandée au cluster Kapsule. Non vérifiée contre la liste des versions réellement proposées par Scaleway aujourd'hui (pas de compte pour interroger `scw k8s version list`) : à confirmer avant le premier `apply`, voir README."
  type        = string
  default     = "1.30"
}

variable "type_noeud" {
  description = <<-EOT
    Type d'instance du pool de nœuds. DEV1-M (3 vCPU, 4 Go de RAM) retenu :
    c'est le plus petit type dont la RAM (4 Go) laisse de la marge au-dessus
    de ce qu'exigent kubelet, les agents système du nœud et un ou deux pods
    edumatch-serve (~256-512 Mi chacun, voir k8s/base/deployment.yaml) sans
    tout occuper. DEV1-S (2 vCPU, 2 Go) a été écarté : sur un nœud à 2 Go, le
    système et un seul pod suffisent à laisser un HPA sans marge pour
    scaler. Prix constaté sur la page tarifaire publique de Scaleway le
    2026-09-15 : environ 0,0202 EUR/heure (~14,74 EUR/mois si le nœud
    tournait un mois complet — il ne tourne que pendant les démonstrations,
    voir la règle de coût).
  EOT
  type        = string
  default     = "DEV1-M"
}

variable "pool_taille_min" {
  description = "Nombre minimal de nœuds du pool, y compris hors démonstration si le cluster n'est pas détruit entre deux (déconseillé, voir README)."
  type        = number
  default     = 1
}

variable "pool_taille_max" {
  description = <<-EOT
    Nombre maximal de nœuds autorisé par l'autoscaler de pool. Fixé à 2 : le
    HPA applicatif (k8s/base/hpa.yaml) monte jusqu'à 6 réplicas
    edumatch-serve au pic — plafond qui matérialise le rapport de charge 1:6
    de la saisonnalité Parcoursup — depuis un plancher de 2 réplicas fixé
    pour la disponibilité (indépendant de ce rapport, voir hpa.yaml). Chaque
    nœud DEV1-M (4 Go) peut porter plusieurs réplicas de 256-512 Mi chacun
    sans qu'un nœud par pod soit nécessaire. 2 nœuds suffisent à accueillir
    6 pods edumatch-serve avec la marge système ; ce n'est pas mesuré sous
    charge réelle (pas de cluster disponible dans cette session), donc une
    valeur prudente plutôt qu'optimisée à l'unité près.
  EOT
  type        = number
  default     = 2
}
