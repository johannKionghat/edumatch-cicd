# Monitoring — Prometheus, Alertmanager, Grafana

Sert les critères 2.8 (surveillance : métriques, alertes, incidents) et 4.13
(monitoring en production : performance, latence, alertes). Cible le
`Deployment edumatch-serve` déployé par `k8s/base/` (voir `k8s/README.md`)
sur le cluster de démonstration Scaleway Kapsule (`terraform/`) — ni l'un ni
l'autre n'est modifié depuis ce dossier.

## L'instrumentation de l'API : en place

Prometheus, Grafana et les alertes de ce dossier supposent que l'API expose une route
`/metrics`. **C'est le cas** : `edumatch-ia` embarque
`prometheus-fastapi-instrumentator>=8.0,<9.0` (`pyproject.toml`) et appelle
`Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)`
dans `src/edumatch/api/main.py`. Les deux métriques métier décrites plus bas existent
également : `edumatch_feedback_decisions_total` et `edumatch_matching_sans_resultat_total`.

L'alerte `EdumatchInstrumentationAbsente` (voir plus bas) garde tout son sens : elle ne
signale plus une instrumentation à écrire, mais une instrumentation qui aurait disparu —
image reconstruite sans la dépendance, route déplacée, collecteur mal configuré.

Les sections qui suivent décrivent ce code parce que le monitoring de ce dépôt ne peut pas
s'expliquer sans lui : `edumatch-cicd` ne modifie jamais `edumatch-ia`. Elles décrivent
l'état en place, pas un geste à faire.

### 1. Dépendance — `edumatch-ia/pyproject.toml`

```toml
"prometheus-fastapi-instrumentator>=8.0,<9.0",
```

La borne haute n'est pas une précaution de principe : elle vient d'une panne réelle. La
série 7.x s'appuie sur une structure interne de FastAPI qui a changé en 0.141 — dans cette
version, `app.routes` expose un routeur inclus par `include_router()` là où 7.x attend une
route, et l'instrumentation échoue au démarrage. La borne basse écarte donc la série
cassée, et la borne haute interdit qu'une 9.x inconnue entre sans être testée.

Alternative écartée : instrumenter à la main avec `prometheus_client` seul
(créer soi-même les histogrammes de latence, les compteurs par route et par
code de statut, le middleware ASGI qui les alimente). `prometheus-fastapi-instrumentator`
fait exactement cela, avec des noms de métriques déjà conventionnels
(`http_request_duration_seconds`, `http_requests_total`) — ceux que
`monitoring/prometheus/alerts.yaml` et le tableau de bord Grafana
interrogent. Réécrire l'équivalent à la main n'apporterait rien de plus pour
un service à quatre routes, et risquerait de nommer les métriques
différemment de la convention Prometheus la plus répandue (compatibilité
avec n'importe quel exemple public de règle d'alerte). Le seuil qui ferait
reconsidérer ce choix : un besoin de métriques très spécifiques au domaine
qui ne rentrent pas dans le modèle « une requête HTTP, une route, un code »
— pas le cas de `/matching`, `/explain`, `/feedback`, `/health`.

### 2. Exposition — `edumatch-ia/src/edumatch/api/main.py`

Le code ci-dessous est celui en place dans `create_app()`.

```python
from prometheus_fastapi_instrumentator import Instrumentator

def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(...)  # inchangé
    enregistrer_gestionnaires_erreurs(app)
    app.include_router(health.router)
    app.include_router(matching.router)
    app.include_router(explain.router)
    app.include_router(feedback.router)
    app.include_router(assistant.router)
    app.include_router(ecran.router)

    # Instrumentation Prometheus : doit être appelée avant expose(), et après
    # que toutes les routes dont on veut mesurer la latence sont enregistrées
    # — sinon `handler` resterait "none" pour elles dans les métriques.
    # `/metrics` n'apparaît jamais dans le schéma OpenAPI public
    # (`include_in_schema=False`) : ce n'est pas une route fonctionnelle pour
    # un conseiller, seulement un point de collecte pour Prometheus.
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    app.mount("/static", StaticFiles(directory=DOSSIER_STATIQUE), name="static")
    return app
```

Point d'attention à vérifier au moment de coller ce code : `Instrumentator()`
regroupe par défaut les chemins par le **gabarit de route** FastAPI
(`/matching`, pas une valeur d'URL avec un identifiant dedans) — aucune des
quatre routes de cette API ne porte de paramètre de chemin variable
aujourd'hui, donc pas de risque de cardinalité incontrôlée sur le label
`handler`. Si une route à paramètre (`/formation/{id}`) apparaissait un
jour, vérifier que le regroupement par gabarit reste actif avant de
déployer — sinon chaque identifiant produirait sa propre série temporelle.

### 3. Deux métriques métier, en place

Ni obligatoires pour que les alertes et le tableau de bord de base fonctionnent (ils ne
dépendent que de l'étape 2), ni couvertes par l'instrumentation HTTP générique — elles
portent une information que seul le code métier connaît. Les deux existent dans
`edumatch-ia` : `edumatch_feedback_decisions_total` et
`edumatch_matching_sans_resultat_total`.

**`edumatch-ia/src/edumatch/api/routes/feedback.py`** — compte les décisions
d'un conseiller par type (retenu / écarté), le signal le plus direct du
contrôle humain exigé par l'article 14 de l'AI Act :

```python
from prometheus_client import Counter

_FEEDBACK_DECISIONS = Counter(
    "edumatch_feedback_decisions_total",
    "Décisions de conseiller enregistrées via /feedback, par type de décision.",
    ["decision"],
)

# dans la fonction feedback(), après journal.enregistrer(...) :
_FEEDBACK_DECISIONS.labels(decision=requete.decision).inc()
```

**`edumatch-ia/src/edumatch/api/routes/matching.py`** — compte les requêtes
pour lesquelles aucune formation ne correspond au profil (cellule vide), un
signal produit (combinaisons trop restrictives), pas un signal
d'infrastructure :

```python
from prometheus_client import Counter

_MATCHING_SANS_RESULTAT = Counter(
    "edumatch_matching_sans_resultat_total",
    "Requêtes /matching pour lesquelles aucune formation ne correspond au profil.",
)

# dans matching(), dans la branche `if n_disponibles == 0:`, avant `return reponse` :
_MATCHING_SANS_RESULTAT.inc()
```

Sans ces deux ajouts, les deux panneaux « Métier » du tableau de bord Grafana
(`grafana/dashboards/edumatch-api.json`) restent vides — documenté comme tel
dans leur description, pas masqué.

## Ce que ce dossier livre

```
monitoring/
├── README.md                       ce fichier
├── slo.md                          SLO déclaré, budget d'erreur, pourquoi la moyenne ment
├── prometheus/
│   ├── namespace.yaml              espace de noms "monitoring", séparé de "edumatch"
│   ├── rbac.yaml                   ServiceAccount + ClusterRole/Binding en lecture seule
│   ├── configmap.yaml              prometheus.yml — découverte par Service, pas par annotation
│   ├── alerts.yaml                 les 5 règles d'alerte, chacune avec son "action"
│   └── deployment.yaml             Deployment + Service Prometheus
├── alertmanager/
│   ├── configmap.yaml              routage — récepteur "null" documenté, pas de webhook
│   └── deployment.yaml             Deployment + Service Alertmanager
└── grafana/
    ├── provisioning-datasource.yaml
    ├── provisioning-dashboards.yaml
    ├── dashboards/edumatch-api.json   le tableau de bord (chargé via ConfigMap --from-file)
    └── deployment.yaml              Deployment + Service Grafana
```

## Le mode de déploiement retenu, et pourquoi

**Retenu : manifestes Kubernetes bruts** (images officielles épinglées, ni
Helm ni opérateur), dans ce dossier.

**Écarté : `kube-prometheus-stack` (Helm)**, la pile communautaire la plus
répandue pour ce besoin. Elle apporte l'opérateur Prometheus (CRD
`ServiceMonitor`, `PrometheusRule`, auto-découverte déclarative), `node-exporter`
et `kube-state-metrics` pour les métriques de cluster, et des tableaux de
bord préconstruits. Trois raisons de l'écarter ici, chiffrées :

1. **Un seul service à surveiller.** L'intérêt de l'opérateur — ajouter un
   `ServiceMonitor` par nouveau service sans toucher à la configuration
   centrale de Prometheus — ne joue pas quand il n'existe qu'un service
   (`edumatch-serve`). Un fichier `scrape_configs` unique
   (`prometheus/configmap.yaml`) fait la même chose pour un coût de
   maintenance nul tant qu'un deuxième service n'apparaît pas.
2. **Le coût mémoire sur un pool à deux nœuds de 4 Go.** L'opérateur, `node-exporter`
   (un pod par nœud) et `kube-state-metrics` ajoutent, à eux seuls et avant même
   Prometheus, Alertmanager et Grafana, de l'ordre de 300 à 500 Mi de requêtes
   mémoire cumulées sur un cluster de cette taille — une fraction significative
   des 4 Go d'un nœud `DEV1-M` (`terraform/variables.tf`), à mettre en
   concurrence avec les réplicas `edumatch-serve` que le HPA peut monter
   jusqu'à 6 (`hpa.yaml`). Aucun des deux composants n'est nécessaire ici : le
   nombre de réplicas prêts se lit directement dans Prometheus par
   `count(up{job="edumatch-serve"} == 1)` (voir plus bas), sans
   `kube-state-metrics`.
3. **Les CRD elles-mêmes.** Installer `ServiceMonitor` et `PrometheusRule`
   ajoute une dépendance de cycle de vie (CRD versionnées, à mettre à jour
   avec l'opérateur) pour un bénéfice qui ne se matérialise qu'à partir de
   plusieurs équipes ou plusieurs services gérant leur propre surveillance de
   façon autonome — pas la situation d'un projet d'une personne avec un seul
   service applicatif.

**Seuil qui ferait reconsidérer ce choix** : plusieurs services Kubernetes à
surveiller, chacun avec sa propre équipe qui doit pouvoir ajouter sa
surveillance sans modifier un ConfigMap central partagé, ou un besoin réel de
métriques de nœud (CPU/mémoire du nœud lui-même, pas seulement du pod) pour
diagnostiquer un problème d'infrastructure — pas de dérive de code applicatif.
Dans ce cas, `kube-prometheus-stack` (Helm) redevient le bon choix malgré son
coût.

### Empreinte mesurée sur le papier (pas sous charge réelle)

| Composant | `requests` | `limits` |
|---|---|---|
| Prometheus | 100m CPU / 256Mi | 500m CPU / 512Mi |
| Alertmanager | 25m CPU / 64Mi | 100m CPU / 128Mi |
| Grafana | 50m CPU / 128Mi | 200m CPU / 256Mi |
| **Total** | **175m CPU / 448Mi** | **800m CPU / 896Mi** |

À comparer aux réplicas `edumatch-serve` : 200m CPU / 320Mi de `requests`
chacun, jusqu'à 6 au pic (`hpa.yaml`) — soit 1200m CPU / 1920Mi au maximum,
répartis sur 2 nœuds `DEV1-M` (3 vCPU / 4 Go chacun, `terraform/variables.tf`).
Le monitoring (175m/448Mi de requêtes) reste une fraction raisonnable d'un
seul nœud et ne devrait pas empêcher le HPA d'atteindre 6 réplicas. Le cluster
existe depuis le 22 septembre 2026 et les réplicas y consomment 487 et 510 Mio
hors charge (`kubectl top pod`) ; ce qui reste à vérifier est le comportement
sous charge réelle, avec la supervision déployée. Si le pic
saisonnier réel montre une contention, la première option est un
`nodeSelector`/anti-affinité qui isole le monitoring sur un nœud dédié,
pas ajoutée par défaut ici pour ne pas complexifier une démonstration qui n'a
jamais montré ce besoin.

## Les alertes — le critère de cette étape

Détail complet, avec le texte exact de chaque `action`, dans
`prometheus/alerts.yaml`. Résumé :

| Alerte | Sévérité | Symptôme observé | Action en une phrase |
|---|---|---|---|
| `EdumatchLatenceP95Elevee` | warning | p95 `/matching` > 300 ms pendant 5 min | Vérifier le HPA et les ressources avant de suspecter le code |
| `EdumatchTauxErreur5xxEleve` | critical | > 5 % de 5xx pendant 5 min | Lire les journaux, retour arrière si récent déploiement |
| `EdumatchAucunReplicaDisponible` | critical | zéro cible prête depuis 2 min | Diagnostiquer l'état des pods, retour arrière si besoin |
| `EdumatchHpaPlafondAtteintDurablement` | warning | 6 réplicas prêts depuis 15 min | Décider d'agrandir le pool de nœuds ou le plafond du HPA |
| `EdumatchInstrumentationAbsente` | warning | aucune métrique depuis 10 min | Vérifier réseau/instrumentation — voir §1 de ce fichier |

Chacune répond aux trois questions du critère : ce qui se passe (`summary`/`description`),
pourquoi c'est grave (conséquence pour le SLO ou pour l'observabilité
elle-même), quoi faire (`action`, une checklist de commandes, pas une phrase
vague). Cinq alertes, pas davantage — une alerte qui ne changerait rien à la
conduite à tenir serait ignorée, et une alerte ignorée abîme la confiance
dans toutes les autres (voir la note dans `prometheus/alerts.yaml` sur la
dérive, volontairement absente : la métrique qui la porterait n'existe pas
encore).

**Alertes sur les symptômes, pas sur les causes.** Aucune alerte sur
« utilisation CPU élevée » ou « mémoire élevée » en tant que telles : ce sont
des causes possibles, pas des symptômes observables par l'utilisateur.
`EdumatchLatenceP95Elevee` et `EdumatchTauxErreur5xxEleve` sont les deux
symptômes réels ; leurs `action` renvoient vers les causes usuelles (CPU,
mémoire, capacité) sans en faire des alertes séparées qui doubleraient le
signal.

## Déployer (une fois le cluster provisionné et l'API déployée)

```bash
# 0. Le namespace applicatif "edumatch" doit déjà exister et l'API déjà
#    tourner (voir README racine du dépôt) — sinon "up{job=\"edumatch-serve\"}"
#    n'aura simplement aucune série, ce que EdumatchInstrumentationAbsente
#    signalera au bout de 10 minutes.

# 1. Espace de noms et permissions de lecture
kubectl apply -f monitoring/prometheus/namespace.yaml
kubectl apply -f monitoring/prometheus/rbac.yaml

# 2. Configuration Prometheus (collecte + règles d'alerte) et le service lui-même
kubectl apply -f monitoring/prometheus/configmap.yaml
kubectl apply -f monitoring/prometheus/alerts.yaml
kubectl apply -f monitoring/prometheus/deployment.yaml

# 3. Alertmanager
kubectl apply -f monitoring/alertmanager/configmap.yaml
kubectl apply -f monitoring/alertmanager/deployment.yaml

# 4. Grafana — provisioning déclaratif, puis le mot de passe administrateur
#    (jamais en dur, même geste que edumatch-object-storage côté k8s/) et le
#    tableau de bord (chargé par fichier, pas recopié dans un YAML)
kubectl apply -f monitoring/grafana/provisioning-datasource.yaml
kubectl apply -f monitoring/grafana/provisioning-dashboards.yaml
kubectl create secret generic grafana-admin --namespace monitoring \
    --from-literal=admin-password="$(openssl rand -base64 24)"
kubectl create configmap grafana-dashboard-edumatch --namespace monitoring \
    --from-file=edumatch-api.json=monitoring/grafana/dashboards/edumatch-api.json
kubectl apply -f monitoring/grafana/deployment.yaml

# 5. Vérifier
kubectl get pods -n monitoring -w
kubectl port-forward -n monitoring svc/prometheus 9090:9090     # http://localhost:9090/targets — la cible edumatch-serve doit apparaître "UP"
kubectl port-forward -n monitoring svc/alertmanager 9093:9093   # http://localhost:9093 — alertes actives
kubectl port-forward -n monitoring svc/grafana 3000:3000        # http://localhost:3000 — identifiant admin, mot de passe généré à l'étape 4
```

Détruire en même temps que le reste (voir README racine, section
« Détruire ») : `kubectl delete namespace monitoring` avant
`terraform destroy`, comme pour `edumatch`.

## Ce qui est déployé, et ce qui reste à vérifier

Les manifestes de ce dossier ont été appliqués sur le cluster le 25 septembre 2026, par
`python scripts/deploiement.py monitoring`. Observé à cette occasion :

- Prometheus, Alertmanager et Grafana en `1/1 Running` dans l'espace de noms `monitoring`.
- Prometheus découvre trois cibles, toutes en `health: up` : les deux réplicas
  d'`edumatch-serve` et lui-même. La collecte réelle des métriques de l'API est donc établie,
  et l'alerte `EdumatchInstrumentationAbsente` a de quoi se déclencher si la route disparaît.
- Les cinq règles d'alerte sont chargées dans le groupe `edumatch-serve.symptomes`, toutes à
  l'état `inactive` : `EdumatchLatenceP95Elevee`, `EdumatchTauxErreur5xxEleve`,
  `EdumatchAucunReplicaDisponible`, `EdumatchHpaPlafondAtteintDurablement` et
  `EdumatchInstrumentationAbsente`.

Ce qui reste à vérifier : le déclenchement d'une alerte de bout en bout sous charge réelle, et
le comportement du dispositif au pic. Alertmanager n'a toujours aucun destinataire externe,
comme le dit `alertmanager/configmap.yaml` : les alertes sont visibles, elles ne notifient
personne.

- **Validation faite** : chaque fichier YAML de ce dossier (y compris le
  contenu imbriqué des `ConfigMap` — `prometheus.yml`, les règles d'alerte,
  `alertmanager.yml`, les fichiers de provisioning Grafana) a été rechargé
  par un analyseur YAML standard et relu structurellement comme un
  dictionnaire ; le tableau de bord Grafana a été rechargé par un analyseur
  JSON standard. Tous valides syntaxiquement.
- **Non vérifié** : `promtool check rules` n'a pas pu tourner (binaire
  absent de cet environnement) — les règles d'alerte n'ont été validées que
  syntaxiquement (YAML bien formé), pas contre le validateur natif de
  Prometheus (qui vérifierait en plus la syntaxe PromQL des expressions
  `expr`). Premier geste à faire après le premier déploiement :
  `kubectl exec -n monitoring deploy/prometheus -- promtool check rules /etc/prometheus/rules/edumatch-serve.yaml`,
  ou localement avec `promtool` installé.
- **Non vérifié** : les tags d'image (`prom/prometheus:v2.54.1`,
  `prom/alertmanager:v0.27.0`, `grafana/grafana:11.2.0`) contre Docker Hub —
  même réserve déjà posée sur `amazon/aws-cli` dans `k8s/README.md`, à
  reconfirmer avant le premier déploiement.
- **Non vérifié** : que le comportement de Cilium (CNI du cluster,
  `terraform/main.tf`) laisse bien Prometheus (namespace `monitoring`)
  atteindre le port 8000 des pods `edumatch-serve` (namespace `edumatch`) —
  la `NetworkPolicy` existante (`k8s/base/networkpolicy.yaml`) autorise déjà
  l'entrée sur ce port depuis n'importe quel namespace
  (`from: [{namespaceSelector: {}}]`), donc aucune modification n'a été
  nécessaire ici ; reste à confirmer que Cilium applique bien cette règle
  telle qu'écrite (même réserve déjà posée côté `k8s/README.md`).
- **Non mesuré** : les `requests`/`limits` de ce dossier et le seuil des
  alertes de latence/erreur sont posés sur un raisonnement, pas sur une
  charge réelle. À ajuster avec `kubectl top pod -n monitoring` après le
  premier déploiement, comme pour `k8s/base/`.

## Ce qui reste hors de ce dossier, assumé

- **La dérive du modèle en alerte Prometheus** : le seuil (PSI médian, 0,20)
  est mesuré et documenté côté `edumatch-ia` (ADR 0018), mais tourne
  aujourd'hui comme un rapport batch (`make derive`), pas comme un processus
  qui expose une métrique consultable en continu. L'alerte rejoindra
  `prometheus/alerts.yaml` le jour où le DAG Airflow de réentraînement
  pousse ce résultat vers un Pushgateway (ou expose lui-même `/metrics`) sur
  le cluster — pas avant, pour ne pas écrire une règle qui ne se déclenche
  jamais faute de métrique.
- **La notification réelle des alertes** (Slack, e-mail) : `alertmanager/configmap.yaml`
  route tout vers un récepteur explicitement sans destination
  (`sans-notification`). Les alertes restent visibles dans l'IHM Alertmanager
  et dans Grafana ; brancher un vrai récepteur est documenté dans le
  commentaire du fichier, pas fait ici faute de canal réel pour ce projet de
  certification.
- **La panne provoquée et sa reprise, filmées** : ce dossier fournit
  l'instrumentation qui permettra de voir, en direct, une alerte se
  déclencher puis se résoudre — l'exercice lui-même (couper un pod, saturer
  le CPU, observer `EdumatchAucunReplicaDisponible` se déclencher puis se
  résorber) reste une étape suivante, qui a besoin d'un cluster réel pour
  exister.
