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
