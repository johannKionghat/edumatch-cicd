# SLO déclaré — API de matching

Critère 4.13 (monitoring en production : performance, latence, alertes) et le
principe SLI/SLO/budget d'erreur du plan de surveillance.

## Pourquoi une moyenne mentirait ici

Une latence moyenne de 100 ms peut très bien masquer 5 % de requêtes à 3
secondes — et ce sont précisément ces 5 % qu'un conseiller retient, pas la
moyenne. Le calcul est agrégé et lisse tout ce qui dépasse : une moyenne ne
distingue pas « toutes les requêtes sont à 100 ms » de « 95 % sont à 20 ms et
5 % sont à 1,6 s », alors que ces deux situations n'ont rien à voir du point
de vue de l'utilisateur. C'est pour cette raison qu'on surveille des
**percentiles** (p50, p95, p99), jamais une moyenne seule — voir le panneau
« Latence /matching » du tableau de bord Grafana
(`grafana/dashboards/edumatch-api.json`), qui trace les trois côte à côte
plutôt qu'une moyenne unique.

## Les deux SLI retenus, et pourquoi ce sont eux

| SLI | Ce qu'il mesure |
|---|---|
| Latence | p95 de `/matching`, sur fenêtre glissante 5 minutes |
| Disponibilité | proportion de requêtes `/matching` qui ne répondent pas en 5xx |

`/matching` et non `/health` ou `/explain` : c'est la route qui porte
l'usage réel (recommandation pour un profil), celle que `configs/base.yaml`
(`api.slo_latence_p95_ms`) chiffre déjà côté edumatch-ia. `/health` répond en
quelques microsecondes par construction (aucune dépendance lourde, voir son
docstring) et ne dirait rien de la charge réelle. `/explain` sert des
explications précalculées (E25 — SHAP par cellule, servi depuis le stockage,
jamais recalculé à la requête) : sa latence est structurellement plus basse
et moins représentative de la charge que `/matching`, qui filtre et score le
catalogue à chaque appel.

## Les deux objectifs

### 1. Latence — p95 de `/matching` sous 300 ms

Déjà arrêté dans `configs/base.yaml` (`slo_latence_p95_ms: 300`), côté
edumatch-ia — repris ici tel quel, pas redéfini séparément, pour que le seuil
d'alerte Prometheus et le seuil qui borne `max_formations_evaluees`
(le plafond qui empêche une requête à catalogue trop large de dégrader tout
le monde, voir `api/routes/matching.py`) restent la même valeur, à un seul
endroit d'origine.

**Par définition, un p95 sous 300 ms équivaut exactement à : au moins 95 %
des requêtes sont servies en moins de 300 ms.** Le budget d'erreur de latence
s'exprime donc directement en proportion de requêtes, sans calcul
supplémentaire :

> **Budget d'erreur de latence = 5 % des requêtes `/matching` peuvent
> dépasser 300 ms sans que le SLO soit considéré violé.**

Concrètement, sur une séance de démonstration de 1 000 appels à
`/matching`, jusqu'à 50 peuvent dépasser 300 ms avant que le SLO ne soit
franchi — c'est cette marge que `EdumatchLatenceP95Elevee` (`prometheus/alerts.yaml`)
surveille en continu, pas la moyenne.

### 2. Disponibilité — 99,5 % des requêtes `/matching` non-5xx

**Choix assumé, différent de l'exemple à 99,9 % du plan de surveillance
général** : ce dernier chiffre — 43 minutes d'indisponibilité tolérées par
mois — suppose un service qui tourne en continu tout le mois. Ce n'est
explicitement pas le cas ici : le cluster Kapsule n'est provisionné que pour
les démonstrations (règle de coût, `terraform/README.md`), et un budget
d'erreur calculé sur un mois calendaire n'aurait aucun sens pour une
infrastructure volontairement non permanente — il serait soit toujours
« largement dans le budget » (le service est éteint la plupart du temps, donc
n'accumule aucune erreur), soit inutilisable comme signal. Le budget est donc
exprimé **par requête**, pas par durée, ce qui reste valable aussi bien
pendant une démonstration de deux heures que sur un service qui tournerait
en continu demain.

> **Objectif : 99,5 % des requêtes `/matching` répondent sans code 5xx.**
> **Budget d'erreur = 0,5 % des requêtes peuvent échouer sans que le SLO soit
> considéré violé.**

99,5 % et non 99,9 % : `hpa.yaml` impose un plancher de 2 réplicas
applicatifs même au creux de charge, mais le pool de nœuds sous-jacent
(`terraform/variables.tf`, `pool_taille_min: 1`) peut redescendre à un seul
nœud — les deux pods peuvent alors se retrouver colocalisés dessus (l'
anti-affinité de `deployment.yaml` est souple, pas stricte, précisément
pour ne jamais bloquer un déploiement sur un pool à un seul nœud), et la
perte de ce nœud unique reste une coupure totale malgré le plancher de deux
pods. Ajouté à l'absence de tolérance multi-zone
(`terraform/variables.tf`, une seule zone `fr-par-1`), un redéploiement
(`RollingUpdate`, `maxUnavailable: 0` — donc sans coupure en théorie) ou un
`autohealing` de nœud peuvent introduire quelques secondes d'indisponibilité
réelle qu'un objectif à 99,9 % (une erreur sur mille) rendrait quasi
impossible à tenir sincèrement pour une infrastructure de cette taille.
Concrètement : sur 1 000 requêtes, jusqu'à 5 peuvent échouer sans franchir le
seuil — c'est ce que `EdumatchTauxErreur5xxEleve` mesure (seuil fixé à 5 %
instantané sur une fenêtre de 5 minutes pour l'alerte, plus réactif que le
seuil d'objectif à 0,5 % cumulé, volontairement : une alerte doit réagir vite
à une dégradation en cours, pas seulement constater un budget mensuel déjà
dépassé).

## Ce que le budget d'erreur autorise

C'est la marge qui permet de déployer sans figer le service : tant que le
budget n'est pas consommé, une mise à jour risquée (nouvelle version du
modèle, changement de dépendance) reste acceptable. S'il est épuisé, la
priorité passe à la fiabilité avant toute nouvelle fonctionnalité — un
principe SRE standard, appliqué ici à l'échelle d'un projet d'une personne :
le budget d'erreur est ce qui évite de décider « au ressenti » si un incident
est grave.

## Ce qui n'a pas pu être vérifié ici

Ces deux objectifs sont posés sur un raisonnement (taille du service, absence
de redondance multi-zone, nature de la charge), pas mesurés sous trafic réel
— aucun cluster n'a existé à ce jour. Ils sont à confronter aux
premières mesures réelles après le premier déploiement, et à ajuster si le
p95 réel au repos dépasse déjà 300 ms sans charge (auquel cas le problème
serait dans le dimensionnement `requests`/`limits`, pas dans le SLO).
