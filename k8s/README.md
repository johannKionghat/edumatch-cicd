# Manifestes Kubernetes

Manifestes du cluster de démonstration (Scaleway Kapsule, provisionné par
`terraform/`). Dimensionnement (`requests`/`limits`) et `HorizontalPodAutoscaler`
sont posés sur les chiffres du dossier — le rapport de charge 1:6 de la
saisonnalité Parcoursup pour le plafond de réplicas, un plancher de
disponibilité de 2 réplicas indépendant de ce rapport — pas devinés ; voir le
commentaire de chaque fichier pour le raisonnement complet.

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
orchestre l'entraînement, tourne aujourd'hui en local via
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
│   ├── deployment.yaml        API edumatch-serve — requests/limits, sondes, sécurité, anti-affinité (pas de `replicas` : piloté par le HPA)
│   ├── service.yaml           adresse stable ClusterIP
│   ├── hpa.yaml                2 à 6 réplicas selon l'utilisation CPU (plancher de disponibilité, plafond du rapport de charge 1:6)
│   ├── pdb.yaml                budget de perturbation — jamais zéro pod prêt pendant une éviction volontaire (maintenance, drain)
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

## Plancher de réplicas et plafond — deux arguments distincts

`hpa.yaml` fixe `minReplicas: 2` et `maxReplicas: 6`. Ces deux bornes ne
répondent pas à la même question et n'ont aucune raison de partager le même
rapport 1:6 :

- **Le plafond (6)** matérialise la DEMANDE : le rapport de charge mesuré
  entre le pic Parcoursup (janvier-mai) et le creux estival, détaillé dans le
  commentaire de `hpa.yaml`.
- **Le plancher (2)** répond à une exigence de DISPONIBILITÉ, valable même
  au creux de charge : avec un seul réplica, un redémarrage de nœud ou la
  fenêtre d'un `RollingUpdate` laisse un instant sans aucun pod prêt.

`deployment.yaml` ne fixe plus `replicas` du tout : dès qu'un HPA cible un
Deployment, c'est lui qui décide du nombre de réplicas en continu, et
laisser une valeur fixe dans le Deployment provoquerait un conflit à chaque
`kubectl apply` (le champ écraserait la décision du HPA, qui la
recorrigerait ensuite). Conséquence assumée : à la toute première création
de l'objet, Kubernetes applique son propre défaut (1 réplica) jusqu'au
premier tour de réconciliation du HPA — une fenêtre transitoire de quelques
secondes, pas un état durable.

`pdb.yaml` (`PodDisruptionBudget`, `minAvailable: 1`) protège ce plancher
contre les évictions VOLONTAIRES (maintenance planifiée, mise à niveau du
pool, `autohealing` proactif) : jamais zéro pod prêt pendant une telle
opération. Il ne protège pas contre la perte brutale d'un nœud, que
l'anti-affinité souple (`preferredDuringSchedulingIgnoredDuringExecution`)
de `deployment.yaml` atténue seulement quand deux nœuds sont disponibles —
elle ne bloque jamais un déploiement si le pool est réduit à un seul nœud
(`terraform/variables.tf`, `pool_taille_min: 1`).

## Les comptes conseillers : le secret `edumatch-conseillers`

Le conteneur `edumatch-serve` lit `CONSEILLER_COMPTES` dans le secret
`edumatch-conseillers`, clé `comptes`. Sans lui, l'API refuse l'écran conseiller,
`/matching` et `/feedback` : aucune décision ne peut être prise sans être imputable à une
personne identifiée (contrôle humain, article 14 du règlement sur l'IA). Le format est celui
que documente `.env.example` côté edumatch-ia : `identifiant:empreinte`, plusieurs comptes
séparés par `;`.

L'empreinte scrypt se calcule hors ligne, depuis edumatch-ia :

```
python -m edumatch.api.auth "<mot de passe>"
```

puis le secret se crée ainsi :

```
kubectl create secret generic edumatch-conseillers --namespace edumatch   --from-literal=comptes="<identifiant>:<empreinte>"
```

`python scripts/deploiement.py secrets-k8s` le crée aussi, depuis la variable
`CONSEILLER_COMPTES` de `scaleway.env`. Le script refuse une entrée qui n'a pas la forme
d'une empreinte scrypt : un mot de passe recopié en clair par erreur n'atteint jamais le
cluster. Le secret n'est pas `optional` dans le Deployment : s'il manque, le pod reste en
`CreateContainerConfigError`, ce qui se voit immédiatement dans `kubectl get pods`.

## Ce qui a été observé, et ce qui reste à observer

Le cluster a existé les 18 et 19 septembre 2026, et `kubectl` y a été exécuté. Observé à
cette occasion : le nœud unique est passé `Ready` en v1.36.4 environ deux minutes après
l'`apply` Terraform, le namespace `edumatch` et ses deux secrets (accès au stockage objet,
accès au registre) ont été créés, et `kubectl get nodes` répond avec le kubeconfig installé
par `scw k8s kubeconfig install`.

Les manifestes de `base/` ont été appliqués le 22 septembre 2026, avec l'image étiquetée
`1acab97` publiée au registre par `build-images.yml`. Observé depuis : deux pods `Running`,
le conteneur d'initialisation a bien récupéré les artefacts depuis le stockage objet, et
`NetworkPolicy`, `PodDisruptionBudget` et `HorizontalPodAutoscaler` sont en place, ce dernier
à deux réplicas pour un plancher de deux et un plafond de six. Le déploiement a été fait par
`scripts/deploiement.py` ; l'exécution de `deploy.yml`, qui applique les mêmes manifestes,
reste à faire.

La validation faite sur les fichiers eux-mêmes :

- Syntaxe YAML de chaque fichier, vérifiée par un chargeur YAML standard —
  tous valides.
- `kubectl apply --dry-run=client` a été tenté mais ne peut pas fonctionner
  sans cluster joignable (il télécharge le schéma OpenAPI du serveur avant
  de valider, même en mode client) — échec attendu, pas une preuve d'erreur
  dans les manifestes.
- Relecture manuelle ligne par ligne contre la documentation Kubernetes.

Points précis à confirmer au premier déploiement des manifestes, chacun isolé
pour une correction rapide :

- **Le tag de l'image `amazon/aws-cli`** utilisée par le conteneur
  d'initialisation (`base/deployment.yaml`) : confirmé le 22 septembre 2026,
  le conteneur a démarré et synchronisé les artefacts.
- **Le comportement de Cilium (CNI choisi en Terraform) avec les
  `NetworkPolicy` standard** : la politique est appliquée sans erreur sur le
  cluster, son effet de filtrage reste à vérifier par un test d'accès depuis
  un autre espace de noms, planifié avant mise en service, sous la
  responsabilité du responsable sécurité technique (voir
  `base/networkpolicy.yaml`).
- **Les valeurs `requests`/`limits` et le seuil du HPA (60 %)** : posés sur
  un raisonnement. Première mesure sur le cluster, hors charge, par
  `kubectl top pod` : 487 et 510 Mio pour les deux réplicas, soit environ
  80 pour cent de la limite de 640 Mio. La mesure sous charge réelle reste à
  faire, et l'ajustement des valeurs avec elle.

La séquence de commandes pour provisionner, déployer, vérifier et détruire
est dans le `README.md` à la racine du dépôt.
