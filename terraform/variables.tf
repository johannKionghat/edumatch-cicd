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
    charge réelle (pas de cluster disponible à ce jour), donc une
    valeur prudente plutôt qu'optimisée à l'unité près.
  EOT
  type        = number
  default     = 2
}

# ─── Instance dédiée à Airflow (ADR 0019) ────────────────────────────────

variable "type_instance_airflow" {
  description = <<-EOT
    Type de l'instance qui exécute la pile Airflow de production (ADR 0019,
    edumatch-ia). DEV1-L (4 vCPU, 8 Go de RAM) retenu : la documentation
    Airflow 2.9.3 demande au moins 4 Go pour la pile Compose de référence
    (base de métadonnées, ordonnanceur, serveur web) ; 8 Go laissent de la
    marge pour la tâche d'agrégation Sirene en Polars, qui s'exécute dans le
    même conteneur travailleur. DEV1-M (4 Go) a été écarté : il correspond
    exactement au plancher documenté pour Airflow seul, sans aucune marge.
    DEV1-XL (12 Go) reste le repli si la mesure du pic mémoire de la tâche
    d'agrégation l'exige (voir l'ADR, section "Mesures à faire au premier
    lancement"). Prix constaté sur la page tarifaire publique de Scaleway le
    2026-09-15 : environ 0,04284 EUR/heure (~31,27 EUR/mois si l'instance
    tournait un mois complet — elle ne tourne que pendant les séances de
    tournage, voir `airflow_active`).
  EOT
  type        = string
  default     = "DEV1-L"
}

variable "cidr_operateur" {
  description = <<-EOT
    Plage d'adresses IP autorisée à se connecter en SSH (port 22) à
    l'instance Airflow, au format CIDR (ex. "203.0.113.42/32" pour une
    adresse unique). Aucune valeur par défaut n'est fournie : une valeur par
    défaut ouvrirait le port SSH au monde entier (0.0.0.0/0) tant que
    personne n'y penserait, ce qui serait une faute de sécurité par
    omission, pas un détail de configuration. `terraform plan` échoue tant
    que cette variable n'est pas renseignée explicitement — voir
    terraform.tfvars.example et le README pour savoir comment trouver sa
    propre adresse IP publique.
  EOT
  type        = string
}

variable "airflow_active" {
  description = <<-EOT
    Bascule l'existence de toutes les ressources de `airflow.tf`
    (`count = var.airflow_active ? 1 : 0`). `false` par défaut : l'instance
    n'existe et ne se facture que pendant les séances de tournage
    (démonstration filmée, répétition). C'est la même règle de coût que le
    cluster Kapsule (voir terraform/README.md) appliquée à une ressource
    facturée à l'heure plutôt qu'au nœud d'un pool élastique — une
    instance oubliée active coûte de l'ordre de 40 EUR par mois pour rien
    (voir l'ADR 0019 pour le détail du calcul).
  EOT
  type        = bool
  default     = false
}

variable "taille_volume_airflow_go" {
  description = <<-EOT
    Taille en Go du volume bloc additionnel qui porte `/srv/edumatch/data`
    (base de métadonnées Airflow, données Parcoursup et Sirene
    retéléchargées, journaux de tâches). 60 Go par défaut : les données
    mesurées sur le poste de développement pèsent environ 4,7 Go
    (`data/raw/sirene` 4,4 Go, le reste très en dessous), auxquels s'ajoutent
    les images Docker tirées du registre et les fichiers temporaires de
    l'agrégation Polars pendant son exécution — non mesurés précisément,
    d'où une marge large plutôt qu'un dimensionnement à l'unité près. Prix
    constaté sur la page tarifaire publique de Scaleway le 2026-09-15 pour
    du stockage bloc 5K : environ 0,000130 EUR/Go/heure, soit environ 5,70
    EUR pour 60 Go sur
    un mois complet.
  EOT
  type        = number
  default     = 60
}
