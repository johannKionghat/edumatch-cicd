"""Amorçage du bucket d'état : un bucket existant est reconnu, quelle que soit la graphie."""

import importlib.util
from pathlib import Path

_chemin = Path(__file__).resolve().parents[1] / "scripts" / "deploiement.py"
_spec = importlib.util.spec_from_file_location("deploiement", _chemin)
deploiement = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deploiement)


def test_sortie_reelle_du_cli_scw() -> None:
    # Sortie de `scw object bucket list region=fr-par -o json`, relevée le 21/09/2026.
    sortie = [{"BucketArn": None, "BucketRegion": None, "CreationDate": "2026-09-17T22:48:02Z", "Name": "edumatch-tfstate"}]
    assert "edumatch-tfstate" in deploiement.noms_des_buckets(sortie)


def test_graphie_minuscule_toujours_lue() -> None:
    assert deploiement.noms_des_buckets([{"name": "edumatch-tfstate"}]) == {"edumatch-tfstate"}


def test_sortie_vide_ou_inattendue() -> None:
    assert deploiement.noms_des_buckets([]) == set()
    assert deploiement.noms_des_buckets(None) == set()
    assert deploiement.noms_des_buckets([{"autre": 1}, "texte"]) == set()
