# ─── Instance dédiée à Airflow (ADR 0019) ────────────────────────────────
#
# Pourquoi une instance à part et pas un pod sur le cluster Kapsule : le
# calcul est détaillé dans l'ADR 0019 côté edumatch-ia
# (docs/sous-docs-projets/adr/0019-airflow-en-production-sur-instance-dediee.md).
# En résumé : la pile Airflow de référence exige au moins 4 Go de mémoire à
# elle seule, alors que le pool Kapsule a déjà 2 368 Mi réservés par l'API et
# le monitoring sur une capacité brute de 8 192 Mi — avant même le pic de la
# tâche Spark qui agrège Sirene. Et l'exécuteur Kubernetes d'Airflow
# suppose un partage de fichiers entre pods que le stockage bloc Scaleway,
# en accès exclusif à un seul nœud (ReadWriteOnce), ne permet pas sans
# réécrire toutes les entrées-sorties du pipeline vers le stockage objet.
#
# Toutes les ressources de ce fichier portent `count = var.airflow_active ?
# 1 : 0` : l'instance n'existe, et ne se facture, que pendant les séances de
# tournage. C'est la même règle de coût que le cluster Kapsule
# (`terraform/README.md`), appliquée ici à une machine facturée à l'heure.

# ─── Adresse IP publique ─────────────────────────────────────────────────
#
# Une IP flexible plutôt que l'IP dynamique attribuée par défaut à
# l'instance : elle survit à un redémarrage de la machine et c'est sur elle
# que pointe le tunnel SSH documenté dans `outputs.tf`. Sans elle, l'adresse
# changerait à chaque `terraform apply` qui recréerait l'instance, et la
# commande de tunnel donnée au candidat serait fausse la fois suivante.
resource "scaleway_instance_ip" "airflow" {
  count = var.airflow_active ? 1 : 0

  zone = var.zone
  tags = ["edumatch", "airflow", var.environnement]
}

# ─── Groupe de sécurité : moindre privilège ──────────────────────────────
#
# Aucune interface Airflow n'est exposée publiquement (voir la décision :
# « aucune interface exposée publiquement »). Le seul port ouvert en entrée
# est le 22 (SSH), et seulement depuis `var.cidr_operateur` — l'adresse (ou
# la plage) de la personne qui opère la démonstration, jamais 0.0.0.0/0.
# C'est par ce même port que passe le tunnel vers l'interface web
# (`ssh -L 8080:127.0.0.1:8080 …`, voir `outputs.tf`) et la connexion au
# registre de conteneurs pour tirer l'image `edumatch-airflow`.
#
# `inbound_default_policy = "drop"` : tout ce qui n'est pas explicitement
# autorisé en entrée est refusé, plutôt que d'énumérer des interdictions —
# c'est la version "liste blanche" du principe de moindre privilège.
# `outbound_default_policy = "accept"` : l'instance doit pouvoir tirer
# l'image du registre privé, télécharger les millésimes Parcoursup et
# Sirene depuis leurs sources publiques, et écrire dans le bucket
# d'artefacts — aucune de ces destinations n'est connue à l'avance avec
# une liste d'IP stable, donc les restreindre en sortie demanderait un
# pare-feu applicatif que ce projet n'a pas les moyens d'opérer.
resource "scaleway_instance_security_group" "airflow" {
  count = var.airflow_active ? 1 : 0

  name                    = "${var.nom_projet}-airflow-${var.environnement}"
  inbound_default_policy  = "drop"
  outbound_default_policy = "accept"
  # `stateful = true` : les paquets de retour d'une connexion déjà acceptée
  # (la session SSH entrante, ou une connexion sortante initiée par
  # l'instance elle-même) ne sont pas réévalués individuellement contre les
  # règles ci-dessous. Sans cet attribut, une connexion établie pourrait
  # être coupée en cours de route si le pare-feu traitait son trafic de
  # retour comme un nouveau paquet non sollicité — demandé explicitement
  # par la décision (ADR 0019, section « Ce qui reste à construire »).
  stateful = true
  tags     = ["edumatch", "airflow", var.environnement]

  inbound_rule {
    action   = "accept"
    port     = 22
    protocol = "TCP"
    ip_range = var.cidr_operateur
  }
}

# ─── Volume de données ───────────────────────────────────────────────────
#
# Un volume bloc séparé du disque racine, dimensionné par
# `var.taille_volume_airflow_go` (60 Go par défaut). Il porte
# `/srv/edumatch/data` (voir cloud-init/airflow.yaml) : les millésimes
# Parcoursup et Sirene retéléchargés pour la démonstration (~4,7 Go mesurés
# sur le poste de développement, voir l'ADR 0019), la base PostgreSQL de
# métadonnées Airflow, et les journaux de tâches. Séparé du disque racine
# pour pouvoir le redimensionner ou le conserver indépendamment de
# l'instance elle-même — utile si l'instance doit être recréée (panne,
# changement de type) sans retélécharger les données.
resource "scaleway_instance_volume" "airflow_data" {
  count = var.airflow_active ? 1 : 0

  type       = "b_ssd"
  size_in_gb = var.taille_volume_airflow_go
  zone       = var.zone
  tags       = ["edumatch", "airflow", var.environnement]
}

# ─── Instance ─────────────────────────────────────────────────────────────
#
# DEV1-L (4 vCPU, 8 Go) par défaut — voir `variables.tf` pour le calcul du
# choix face à DEV1-M et DEV1-XL. Rattachée au réseau privé déjà créé pour
# le cluster Kapsule (`scaleway_vpc_private_network.edumatch`, main.tf) :
# c'est le même cloisonnement réseau que celui documenté pour le cluster,
# pas un réseau public partagé avec d'autres ressources du projet Scaleway.
resource "scaleway_instance_server" "airflow" {
  count = var.airflow_active ? 1 : 0

  name  = "${var.nom_projet}-airflow-${var.environnement}"
  type  = var.type_instance_airflow
  zone  = var.zone
  tags  = ["edumatch", "airflow", var.environnement]

  # NON VÉRIFIÉ DANS CETTE SESSION (pas de compte Scaleway, pas de binaire
  # `scw` disponible ici) : le libellé exact de l'image Ubuntu LTS proposée
  # par le catalogue d'images Scaleway au moment du premier `apply`. Le nom
  # ci-dessous suit la convention observée dans la documentation publique
  # Scaleway (`ubuntu_jammy` pour Ubuntu 22.04 LTS), mais elle évolue avec le
  # temps (nouvelles LTS, retrait d'anciennes images). À confirmer avec
  # `scw instance image list zone=fr-par-1 | grep -i ubuntu` avant le
  # premier `apply`, et à corriger ici seulement si nécessaire.
  image = "ubuntu_jammy"

  security_group_id = scaleway_instance_security_group.airflow[0].id
  ip_id              = scaleway_instance_ip.airflow[0].id

  additional_volume_ids = [scaleway_instance_volume.airflow_data[0].id]

  private_network {
    pn_id = scaleway_vpc_private_network.edumatch.id
  }

  # Aucun secret dans ce script : les données d'initialisation (`user_data`)
  # d'une instance Scaleway sont lisibles par quiconque accède aux métadonnées
  # de l'instance (endpoint local 169.254.42.42, ou console). Le script
  # installe uniquement Docker et prépare l'arborescence de données ; la
  # connexion au registre privé et les identifiants applicatifs
  # (`.env` d'edumatch-ia) sont déposés après coup, par SSH, jamais ici.
  cloud_init = file("${path.module}/cloud-init/airflow.yaml")

  root_volume {
    volume_type = "b_ssd"
    size_in_gb  = 20 # système seulement ; les données vivent sur le volume additionnel
  }
}

# Non vérifié dans cette session : le nom exact de l'attribut de rattachement
# réseau (`private_network { pn_id = ... }`) et l'attribut `ip_id` sur
# `scaleway_instance_server` dans la version 2.83.0 du provider — comme pour
# les autres points déjà signalés dans `main.tf` et `terraform/README.md`,
# à confirmer par `terraform validate` dès que le binaire est disponible.
