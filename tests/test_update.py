"""Contrôles de la lecture des pages civlcomps (update.py) : python tests/test_update.py"""
import sys, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import update as u

LIST = """<table id="tableMain" class="table table-striped" data-count="1"><tbody>
<tr data-key="0"><td>Sep 5, 2026 <br/> Sep 27, 2026</td><td><a class="competition-link" href="https://civlcomps.org/ranking/paragliding-xc/competition?id=9183&amp;ranking_id=1946">Open d&#039;Annecy &amp; Co</a></td><td>PG XC</td><td>1.000</td><td>1.200</td><td>0.578</td><td>0.999</td><td>3</td><td>112</td><td>11158</td><td>157</td><td>25</td><td>0.1</td><td>69.3</td><td>Oct 2, 2026</td></tr>
</tbody></table>"""

COMP = """<span class="label">Number of pilots:</span>
<span class="count">2</span>
<div id="w0" class="ranking-pilots-list"><table><thead>
<tr><th><a href="#">Rank</a></th><th>Pilot Point Factor (Pp)</th><th>Points</th><th>Time devaluated points</th><th><a href="#">Competition score</a></th><th>Pilot</th><th>Nation</th><th><a href="#">CIVL ID</a></th></tr>
</thead><tbody>
<tr class="pilot-item" data-key="1"><td>1</td><td>1.000</td><td>69.400</td><td>69.3</td><td>1732</td><td><a href="#">Sergio Sampaio</a></td><td>BRA</td><td><a href="#">10029</a></td></tr>
<tr class="pilot-item" data-key="2"><td>2</td><td>0.9</td><td>60</td><td>60</td><td></td><td><a class="text-dark bold" href="#">Jos&eacute; Luis<span class="ml-1 text-small text-success">Sport</span></a></td><td>ESP</td><td><a href="#">94347</a></td></tr>
</tbody></table></div>"""


def test_parse_list():
    [c] = u.parse_list(LIST)
    assert c == dict(id=9183, name="Open d'Annecy & Co", start=dt.date(2026, 9, 5), end=dt.date(2026, 9, 27),
                     pilots=112, updated="Oct 2, 2026")
    assert c["start"].strftime(u.DATE) == "Sep 05, 2026"          # même format que la base


def test_parse_competition():
    rows = u.parse_competition(COMP)
    # « Sport » reste collé au nom, comme dans la base d'origine (nettoyé par pgrank/load.py)
    assert rows == [("1", "Sergio Sampaio", "BRA", "10029", "1732"), ("2", "José LuisSport", "ESP", "94347", "")]


def test_incomplete_page_rejected():
    for page in (COMP.replace('class="count">2', 'class="count">3'), LIST.replace('data-count="1"', 'data-count="2"')):
        try:
            (u.parse_competition if "pilot-item" in page else u.parse_list)(page)
        except RuntimeError:
            continue
        raise AssertionError("page incomplète acceptée")


if __name__ == "__main__":
    test_parse_list()
    test_parse_competition()
    test_incomplete_page_rejected()
    print("ok")
