# Rien de sensible ici : les identifiants de connexion (kubeconfig) se
# récupèrent par une commande dédiée (voir README), pas par une sortie
# Terraform qui finirait dans les journaux de CI ou l'historique de state
# en clair.

output "cluster_id" {
  description = "Identifiant du cluster Kapsule — utilisé par `scw k8s kubeconfig get`."
  value       = scaleway_k8s_cluster.edumatch.id
}

output "cluster_status" {
  description = "État du cluster tel que rapporté par l'API Scaleway au dernier apply."
  value       = scaleway_k8s_cluster.edumatch.status
}

output "registre_endpoint" {
  description = "URL du registre de conteneurs — à recopier dans la variable GitHub Actions SCW_REGISTRY_ENDPOINT (dépôt edumatch-cicd) si elle diffère de ce qui a été créé à la main à l'étape CI/CD. Nom d'attribut (`endpoint`) non vérifié contre la version épinglée du provider (pas de `terraform validate` exécutable ici) : si `terraform plan` le rejette, remplacer par `scaleway_registry_namespace.edumatch.id` combiné au domaine documenté par Scaleway (`rg.<région>.scw.cloud/<nom>`)."
  value       = scaleway_registry_namespace.edumatch.endpoint
}

output "bucket_artefacts" {
  description = "Nom du bucket Object Storage où déposer catalogue_predictions.parquet et explications_locales.parquet avant une démonstration (voir README)."
  value       = scaleway_object_bucket.artefacts.name
}

output "reseau_prive_id" {
  description = "Identifiant du réseau privé auquel le cluster est rattaché."
  value       = scaleway_vpc_private_network.edumatch.id
}

# ─── Instance Airflow (ADR 0019) ─────────────────────────────────────────
#
# Vides (liste vide indexée) quand `airflow_active = false` : ces deux
# sorties n'ont de sens que pendant une séance de tournage. Rien de
# sensible ici non plus — une adresse IP publique et une commande SSH ne
# sont pas des secrets, à condition que le groupe de sécurité associé
# n'autorise que `var.cidr_operateur` (voir airflow.tf).

output "airflow_ip_publique" {
  description = "Adresse IP publique de l'instance Airflow, si `airflow_active = true`. Sert uniquement à ouvrir le tunnel SSH ci-dessous — aucune interface n'est jamais exposée directement sur cette IP."
  value       = var.airflow_active ? scaleway_instance_ip.airflow[0].address : null
}

output "airflow_commande_tunnel" {
  description = "Commande exacte pour atteindre l'interface web Airflow (port 8080 sur l'instance) depuis le poste de l'opérateur, sans jamais exposer ce port publiquement. Une fois le tunnel ouvert, l'interface est jointe sur http://localhost:8080 côté poste local."
  value       = var.airflow_active ? "ssh -L 8080:127.0.0.1:8080 ubuntu@${scaleway_instance_ip.airflow[0].address}" : null
}
