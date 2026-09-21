"""Amorçage du bucket d'état : un bucket existant est reconnu, quelle que soit la graphie."""

import importlib.util
from pathlib import Path

import pytest

_chemin = Path(__file__).resolve().parents[1] / "scripts" / "deploiement.py"
_spec = importlib.util.spec_from_file_location("deploiement", _chemin)
deploiement = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deploiement)


def test_sortie_reelle_du_cli_scw() -> None:
    # Sortie de `scw object bucket list region=fr-par -o json`, relevée le 21/09/2026.
    sortie = [
        {
            "BucketArn": None,
            "BucketRegion": None,
            "CreationDate": "2026-09-17T22:48:02Z",
            "Name": "edumatch-tfstate",
        }
    ]
    assert "edumatch-tfstate" in deploiement.noms_des_buckets(sortie)


def test_graphie_minuscule_toujours_lue() -> None:
    assert deploiement.noms_des_buckets([{"name": "edumatch-tfstate"}]) == {
        "edumatch-tfstate"
    }


def test_sortie_vide_ou_inattendue() -> None:
    assert deploiement.noms_des_buckets([]) == set()
    assert deploiement.noms_des_buckets(None) == set()
    assert deploiement.noms_des_buckets([{"autre": 1}, "texte"]) == set()


# ─── Comptes conseillers (secret edumatch-conseillers) ──────────────────────

_EMPREINTE = "scrypt$" + "3f" * 16 + "$" + "a1" * 32


def test_comptes_valides_renvoient_les_identifiants() -> None:
    brut = f"jkionghat:{_EMPREINTE}; conseiller.b:{_EMPREINTE}"
    assert deploiement.valider_comptes_conseillers(brut) == [
        "jkionghat",
        "conseiller.b",
    ]


@pytest.mark.parametrize(
    "brut",
    [
        "jkionghat:MonMotDePasseEnClair",  # mot de passe à la place de l'empreinte
        "jkionghat",  # empreinte absente
        f":{_EMPREINTE}",  # identifiant absent
        "   ",  # vide
        f"a:{_EMPREINTE};a:{_EMPREINTE}",  # doublon
        "a:scrypt$" + "0" * 32 + "$" + "0" * 64,  # empreinte factice du gabarit
    ],
)
def test_comptes_invalides_refuses(brut: str) -> None:
    with pytest.raises(deploiement.ErreurDeploiement):
        deploiement.valider_comptes_conseillers(brut)


def test_le_message_ne_cite_jamais_la_valeur() -> None:
    with pytest.raises(deploiement.ErreurDeploiement) as erreur:
        deploiement.valider_comptes_conseillers("jkionghat:MonMotDePasseEnClair")
    assert "MonMotDePasseEnClair" not in str(erreur.value)


def test_secrets_k8s_cree_le_secret_sans_afficher_les_comptes(
    monkeypatch, capsys
) -> None:
    comptes = f"jkionghat:{_EMPREINTE}"
    monkeypatch.setenv("SCW_ACCESS_KEY", "SCWACCES")
    monkeypatch.setenv("SCW_SECRET_KEY", "cle-secrete")
    monkeypatch.setenv("SCW_REGISTRY_ENDPOINT", "rg.fr-par.scw.cloud/edumatch")
    monkeypatch.setenv("CONSEILLER_COMPTES", comptes)
    appels = []
    monkeypatch.setattr(deploiement.subprocess, "run", _faux_run(appels))
    assert deploiement.cmd_secrets_k8s(None) == 0
    creation = [a for a in appels if "edumatch-conseillers" in a]
    assert creation and f"--from-literal=comptes={comptes}" in creation[0]
    sortie = capsys.readouterr().out
    assert _EMPREINTE not in sortie and "cle-secrete" not in sortie
    assert "Comptes conseillers : 1 (jkionghat)" in sortie


def test_secrets_k8s_refuse_sans_comptes(monkeypatch) -> None:
    monkeypatch.setenv("SCW_ACCESS_KEY", "SCWACCES")
    monkeypatch.setenv("SCW_SECRET_KEY", "cle-secrete")
    monkeypatch.setenv("SCW_REGISTRY_ENDPOINT", "rg.fr-par.scw.cloud/edumatch")
    monkeypatch.delenv("CONSEILLER_COMPTES", raising=False)
    monkeypatch.setattr(deploiement.subprocess, "run", _faux_run([]))
    with pytest.raises(deploiement.ErreurDeploiement, match="CONSEILLER_COMPTES"):
        deploiement.cmd_secrets_k8s(None)


def _faux_run(appels: list):
    """Remplace kubectl : enregistre les arguments, renvoie un manifeste vide."""

    class _Resultat:
        returncode = 0
        stdout = "apiVersion: v1\n"
        stderr = ""

    def run(commande, *args, **kwargs):
        appels.append(list(commande))
        return _Resultat()

    return run
