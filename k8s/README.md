# Manifestes Kubernetes

Manifestes du cluster de démonstration (Scaleway Kapsule, provisionné par
`terraform/`). Dimensionnement (`requests`/`limits`) et `HorizontalPodAutoscaler`
sont posés sur les chiffres du dossier — le rapport de charge 1:6 de la
saisonnalité Parcoursup — pas devinés ; voir le commentaire de chaque fichier
pour le raisonnement complet.

## Convention attendue par `deploy.yml`

Le workflow de déploiement substitue deux jetons littéraux dans tous les
fichiers `.yaml`/`.yml` de `k8s/base/` avant de les appliquer :

| Jeton | Remplacé par |
|---|---|
| `__EDUMATCH_SERVE_IMAGE__` | `<registre>/edumatch-serve:<empreinte-courte-du-commit>` |
| `__EDUMATCH_TRAIN_IMAGE__` | `<registre>/edumatch-train:<empreinte-courte-du-commit>` |

`__EDUMATCH_SERVE_IMAGE__` apparaît dans `base/deployment.yaml`.
`__EDUMATCH_TRAIN_IMAGE__` n'apparaît dans aucun fichier à ce stade : rien ne
consomme encore l'image d'entraînement sur ce cluster — Airflow, qui
orchestre l'entraînement (E33), tourne aujourd'hui en local via
`docker-compose.yml` côté edumatch-ia, pas sur Kapsule. Faire tourner
l'entraînement sur le cluster de démonstration (un `CronJob` Kubernetes, ou
un `KubernetesPodOperator` déclenché par un Airflow lui-même déployé sur le
cluster) est une extension possible, pas faite ici : elle ajouterait un
stockage persistant pour les artefacts en sortie et une politique de retenue
des jobs terminés, hors du périmètre serré de cette étape (servir l'API).
`deploy.yml` continue de fonctionner sans erreur si ce jeton n'apparaît
nulle part — son `sed` ne fait simplement rien.

## Ce qui est livré ici

```
k8s/
├── base/
│   ├── namespace.yaml         espace de noms dédié (cloisonnement, critère 2.7)
│   ├── serviceaccount.yaml    compte de service dédié, aucun jeton monté
│   ├── configmap.yaml         configuration non sensible (EDUMATCH_ENV, chemins, bucket)
│   ├── deployment.yaml        API edumatch-serve — requests/limits, sondes, sécurité
│   ├── service.yaml           adresse stable ClusterIP
│   ├── hpa.yaml                1 à 6 réplicas selon l'utilisation CPU
│   └── networkpolicy.yaml     cloisonnement réseau entre pods
└── secret.example.yaml        gabarit de secrets — jamais appliqué automatiquement
```

Le dossier `overlays/` prévu à l'étape précédente n'a pas été conservé :
`deploy.yml` applique `k8s/base/*.yaml` directement, sans passer par
`kustomize`. Ajouter une couche `overlays/demo/` aurait dupliqué ce que
`base/` fait déjà pour un seul environnement existant (voir
`terraform/variables.tf`, `environnement = "demo"` est la seule valeur
acceptée aujourd'hui) — un raffinement à réserver au jour où un second
environnement Kubernetes apparaît réellement, pas avant.

## requests / limits — pourquoi ils ne sont jamais vides

Voir le commentaire détaillé dans `base/deployment.yaml`, au-dessus du bloc
`resources:` du conteneur `edumatch-serve`. En résumé : sans `requests`,
l'ordonnanceur place le pod sans connaître son besoin réel ; sans `limits`,
un conteneur peut consommer toute la ressource disponible sur son nœud et
affamer ses voisins, et un dépassement de la limite mémoire (pas CPU) se
termine par `OOMKilled`. Les deux sont définis, volontairement différents
(classe de qualité de service *Burstable*, pas *Guaranteed*) : la charge
d'une requête `/matching` est légère (lecture d'un DataFrame déjà en
mémoire), donc la consommation moyenne devrait rester proche de `requests`,
avec de la marge (`limits`) pour un pic de requêtes concurrentes.

## Sondes — la limite assumée

`liveness` et `readiness` interrogent toutes les deux `/health` — voir le
détail dans `base/deployment.yaml`. `/health` (côté edumatch-ia,
`routes/health.py`) est volontairement une sonde de vivacité pure, sans
dépendance lourde : elle ne dit jamais si le catalogue de prédictions ou le
précalcul SHAP ont bien été chargés (`api/state.py`), seulement que le
processus répond. Un pod peut donc passer `readiness` sans que `/matching`
réponde utilement. C'est une limite du dépôt applicatif (edumatch-ia), pas
de ce manifeste — que je ne modifie jamais depuis ici. La correction propre
serait une route `/ready` dédiée côté API, qui vérifierait
`app.state.etat_matching is not None` ; à proposer côté edumatch-ia dans une
prochaine itération, pas faite aujourd'hui.

## Ce qui n'a pas pu être vérifié ici

Ni `terraform apply` ni `kubectl apply` n'ont été exécutés : aucun cluster
n'existe. La validation faite dans cette session :

- Syntaxe YAML de chaque fichier, vérifiée par un chargeur YAML standard —
  tous valides.
- `kubectl apply --dry-run=client` a été tenté mais ne peut pas fonctionner
  sans cluster joignable (il télécharge le schéma OpenAPI du serveur avant
  de valider, même en mode client) — échec attendu, pas une preuve d'erreur
  dans les manifestes.
- Relecture manuelle ligne par ligne contre la documentation Kubernetes.

Points précis à reconfirmer avant le premier déploiement réel, chacun isolé
pour une correction rapide :

- **Le tag de l'image `amazon/aws-cli`** utilisée par le conteneur
  d'initialisation (`base/deployment.yaml`) — non vérifié qu'il existe tel
  quel sur Docker Hub au moment de la démonstration.
- **Le comportement de Cilium (CNI choisi en Terraform) avec les
  `NetworkPolicy` standard** — documenté par défaut, pas observé (voir
  `base/networkpolicy.yaml`).
- **Les valeurs `requests`/`limits` et le seuil du HPA (60 %)** — posés sur
  un raisonnement, pas mesurés sous charge réelle faute de cluster ; à
  ajuster avec `kubectl top pod` après le premier déploiement.

La séquence de commandes pour provisionner, déployer, vérifier et détruire
est dans le `README.md` à la racine du dépôt.
