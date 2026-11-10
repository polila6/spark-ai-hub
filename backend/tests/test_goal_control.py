"""Goal control of workspace turns (no «escribe continúa», goal audit), loop guard, research tools (papers, BibTeX, page
pictures) and the image search ranking. Pure logic with fake network."""
import pytest

from app.services import loopguard, media_fetch, research_tools, web
from app.services.agent import (AUDIT_MIN_STEPS, GOAL_DONE, MAX_AUDITS, MAX_ROLLOVERS_WORKSPACE, NUDGES_ROW_WORKSPACE,
                                Runner, continue_reason, hands_off, normalize_plan, nudge_message)
from app.services.director import Ask, decide


# ------------------------------------------------------------------ hand-off phrases

@pytest.mark.parametrize("text", [
    "Escribe **«continúa»** y retomo exactamente por el paso 4",
    "Escribe «continúa» para que siga",
    "¿Quieres que continúe con el capítulo 3?",
    "Si quieres, sigo con la revisión",
    "Write continue to go on",
    "Respóndeme «continuar».",
    "¿Sigo con las figuras?",
])
def test_handoff_phrases_are_detected(text):
    assert hands_off(text)


@pytest.mark.parametrize("text", [
    "He terminado. El informe está en informe/main.pdf (263 páginas).",
    "¿Qué estilo de citas prefieres, APA o Vancouver?",  # a real decision only the user can take
    "",
])
def test_real_endings_and_real_questions_are_not_handoffs(text):
    assert not hands_off(text)


def test_continue_reason_handoff_beats_plan_and_row_limit_is_larger_in_workspaces():
    open_plan = normalize_plan([{"content": "Compilar", "status": "in_progress"}])
    assert continue_reason("Escribe «continúa» y sigo.", open_plan, False, 0, 0, 60, NUDGES_ROW_WORKSPACE) == "handoff"
    assert continue_reason("Hecho.", open_plan, False, 4, 4, 60, NUDGES_ROW_WORKSPACE) == "plan"  # still insisting
    assert continue_reason("Hecho.", open_plan, False, NUDGES_ROW_WORKSPACE, 5, 60, NUDGES_ROW_WORKSPACE) is None
    assert continue_reason("Hecho.", open_plan, False, 2, 2, 6) is None  # chat keeps two in a row


def test_audit_nudges_ask_for_proof_and_the_sentinel_title():
    plan = normalize_plan([{"content": "Revisar PDF", "status": "in_progress"}])
    es = nudge_message("audit", plan, "es")
    assert "AUDITORÍA" in es and "Objetivos cumplidos" in es and "Revisar PDF" in es and "continúa»" in es
    assert "Goals met" in nudge_message("audit", plan, "en")
    assert "Objetivos cumplidos" in nudge_message("audit_fail", plan, "es")
    assert "continúa" in nudge_message("handoff", plan, "es").lower() and "UNA pregunta" in nudge_message("handoff", plan, "es")
    assert GOAL_DONE in "✅ objetivos cumplidos"
    assert AUDIT_MIN_STEPS >= 3 and MAX_AUDITS >= 2 and MAX_ROLLOVERS_WORKSPACE >= 1


def test_director_keeps_the_web_on_in_a_workspace_even_for_a_bare_continue():
    ask = Ask(content="continúa", level=2, available={"docs", "web", "code", "skills", "agents"}, workspace=True, director=True)
    d = decide(ask)
    assert d.web and d.code and d.agents
    off = Ask(content="continúa", level=2, available={"web", "code"}, workspace=True, director=True,
              allow={"web": "off"})
    assert not decide(off).web  # an explicit «off» still wins


def test_image_policy_sends_photos_to_the_internet_and_charts_to_the_latex_agent():
    es = Runner._image_policy(True, True, True)
    assert "search_images" in es and "extract_images" in es and "graficador-latex" in es and "NUNCA dibujes" in es
    assert "imagenes/" in es
    assert "search_papers" in es
    off = Runner._image_policy(True, False, True)
    assert "desactivada" in off and "no dibujes" in off
    assert "graficador-latex" in Runner._image_policy(False, True, True)


# ------------------------------------------------------------------ loop guard

LOOP = "Wait, let me check the file list again. Actually I should verify the path of the figures before writing. "


def test_loopguard_cuts_a_repeating_reasoning_quickly():
    g = loopguard.LoopGuard()
    hit = None
    for i in range(0, len(LOOP * 40), 80):
        hit = g.feed((LOOP * 40)[i:i + 80])
        if hit:
            break
    assert hit and "misma frase" in hit
    assert g.total < 2400  # it does not wait for the whole token budget


def test_loopguard_catches_numbered_variations_and_blocks():
    var = "".join(f"Step {i}: I need to decide whether to use matplotlib or TikZ for the figure number {i % 3}. " for i in range(60))
    assert loopguard.detect(var)
    block = ("The introduction explains the origin of whales, then it lists the fossils, then the anatomy of the ear, "
             "then the hypotheses about the nasal retraction, and finally the open questions of the field today. ") * 9
    assert loopguard.detect(block)


def test_loopguard_leaves_normal_reasoning_alone():
    sentences = [
        "El usuario quiere un informe doctoral, así que primero debo mirar qué hay ya en la carpeta informe.",
        "La figura de la cronología tiene las etiquetas pegadas; probaré a alternarlas arriba y abajo del eje.",
        "Los fósiles de Pakistán y Egipto son la clave del capítulo trece, y falta citar a Gingerich y a Thewissen.",
        "Si compilo con xelatex en vez de pdflatex las fuentes cambian, pero el índice analítico sigue funcionando.",
        "Necesito comprobar el CSV de yacimientos: hay setenta y cuatro filas y dos tienen la latitud vacía.",
        "El mapa paleogeográfico no debería dibujarse a mano; mejor busco uno de Wikimedia con licencia libre.",
        "Después de generar las tablas conviene ejecutar el comprobador del PDF y mirar las páginas anchas.",
        "El capítulo de limitaciones debe ser honesto sobre lo que no se pudo verificar en línea.",
        "Me preocupa que la bibliografía tenga referencias sin DOI; usaré Crossref para completarlas.",
        "Antes de seguir, reviso el registro de LaTeX porque un aviso de caja desbordada puede esconder otro fallo.",
        "La matriz de rasgos tiene veintisiete taxones; el mapa de calor necesita una barra de color legible.",
        "Podría repartir los capítulos entre varios agentes, cada uno con su carpeta de escritura propia.",
        "El glosario ocupa muchas páginas porque cada término lleva su definición y una referencia cruzada.",
        "Voy a dejar la portada para el final, cuando ya sepa cuántas páginas tiene el documento completo.",
        "Un detalle importante: la escala temporal usa millones de años y el eje debe ir invertido.",
        "Para los árboles filogenéticos el paquete forest da mejores resultados que dibujar los nodos a mano.",
        "Tengo que revisar los créditos de las fotografías antes de incluir la sección de agradecimientos.",
        "La compilación tarda cuatro minutos, de modo que conviene compilar capítulos sueltos mientras escribo.",
        "Los pies de figura deben explicar qué se ve, de dónde viene la imagen y bajo qué licencia se usa.",
        "Cuando termine, haré una auditoría punto por punto contra la petición original del usuario.",
    ]
    prose = " ".join(sentences)
    assert len(prose) > 1500 and loopguard.detect(prose) is None
    assert loopguard.detect("corto " * 50) is None  # too short to judge


def test_cut_repeat_keeps_the_text_before_the_loop():
    head = "Primero explico el método con detalle. " * 3
    looped = head + ("Y así sucesivamente, la conclusión es que todo depende del contexto y de los datos disponibles hoy, "
                     "por lo tanto repito la conclusión anterior sin añadir nada nuevo a lo ya dicho antes. ") * 5
    cut = loopguard.cut_repeat(looped)
    assert cut.startswith("Primero explico") and len(cut) < len(looped) * 0.5


# ------------------------------------------------------------------ research tools

def test_openalex_abstract_is_rebuilt_from_the_inverted_index():
    inv = {"Whales": [0], "evolved": [1], "from": [2], "land": [3], "mammals": [4]}
    assert research_tools._abstract(inv) == "Whales evolved from land mammals"
    assert research_tools._abstract(None) == ""
    assert research_tools._doi("https://doi.org/10.1038/abc123") == "10.1038/abc123"


def test_search_papers_falls_back_to_crossref(monkeypatch):
    def fake(url, what):
        if "openalex" in url:
            raise research_tools.ToolError("OpenAlex respondió 500")
        return {"message": {"items": [{"DOI": "10.1234/x", "title": ["Origin of whales"], "author": [{"given": "A", "family": "B"}],
                                       "issued": {"date-parts": [[2007]]}, "container-title": ["Nature"], "type": "journal-article",
                                       "is-referenced-by-count": 12}]}}
    monkeypatch.setattr(research_tools, "_get_json", fake)
    res = research_tools.search_papers("whale origin")
    assert res[0]["doi"] == "10.1234/x" and res[0]["source"] == "Crossref" and res[0]["year"] == 2007
    assert "Origin of whales" in research_tools.format_paper(1, res[0])
    with pytest.raises(research_tools.ToolError):
        research_tools.search_papers("  ")


def test_bibtex_validates_the_doi_and_merge_does_not_duplicate(monkeypatch):
    with pytest.raises(research_tools.ToolError):
        research_tools.bibtex("not a doi")
    monkeypatch.setattr(web, "fetch", lambda *a, **k: {"status": 200, "body": b"@article{Thewissen2007, title={X}}", "url": a[0],
                                                       "content_type": "application/x-bibtex"})
    e = research_tools.bibtex("https://doi.org/10.1234/abc")
    assert research_tools.bib_key(e) == "Thewissen2007"
    text, added, skipped = research_tools.merge_bib("@book{Old2000, title={Y}}\n", [e, e])
    assert added == ["Thewissen2007"] and skipped == ["Thewissen2007"] and text.count("@article") == 1
    monkeypatch.setattr(web, "fetch", lambda *a, **k: {"status": 404, "body": b"", "url": a[0], "content_type": ""})
    with pytest.raises(research_tools.ToolError):
        research_tools.bibtex("10.1234/zzz")


HTML = b"""<html><head><title>Pakicetus - Museum</title>
<meta property="og:image" content="/img/pakicetus-main.jpg"></head><body>
<img src="/static/logo.png" width="300" height="80">
<figure><img src="/img/skull-small.jpg" width="600" height="400" alt="Skull"><figcaption>Cr\xc3\xa1neo de Pakicetus</figcaption></figure>
<img src="/img/tiny.jpg" width="40" height="40">
<img srcset="/img/big-480.jpg 480w, /img/big-1600.jpg 1600w" src="/img/big-480.jpg" alt="Reconstruction">
<img src="https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Pakicetus.jpg/300px-Pakicetus.jpg" width="300" height="200">
</body></html>"""


def test_page_images_finds_main_picture_captions_and_skips_logos_and_icons(monkeypatch):
    monkeypatch.setattr(web, "fetch", lambda *a, **k: {"status": 200, "body": HTML, "url": "https://museum.example/pakicetus",
                                                       "content_type": "text/html"})
    out = research_tools.page_images("https://museum.example/pakicetus")
    urls = [i["url"] for i in out["images"]]
    assert urls[0] == "https://museum.example/img/pakicetus-main.jpg"  # og:image first
    assert "https://museum.example/img/skull-small.jpg" in urls
    assert any(i["caption"].startswith("Cr") for i in out["images"])
    assert "https://museum.example/img/big-1600.jpg" in urls  # the biggest srcset entry
    assert "https://upload.wikimedia.org/wikipedia/commons/a/ab/Pakicetus.jpg" in urls  # the thumbnail's original
    assert not any("logo" in u or "tiny" in u for u in urls)
    assert out["title"] == "Pakicetus - Museum"


# ------------------------------------------------------------------ image search ranking

def test_relevance_compares_query_words_with_title_and_page():
    good = {"title": "Pakicetus skull fossil.jpg", "description": "", "source": "Wikimedia Commons", "page_url": ""}
    bad = {"title": "Sunset beach holiday.jpg", "description": "", "source": "Openverse", "page_url": ""}
    assert media_fetch.relevance("Pakicetus skull", good)[0] == "alta"
    assert media_fetch.relevance("Pakicetus skull", bad)[0] == "baja"
    assert media_fetch.relevance("cráneo de ballena", {"title": "Craneo ballena.jpg"})[0] in ("alta", "media")  # accents folded


# ------------------------------------------------------------------ the tools through the Runner

def _runner(monkeypatch):
    import uuid

    from app.services import agent

    t = agent.Turn(user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), model_id=uuid.uuid4(), content="x")
    r = agent.Runner(t, lambda e: None, lambda: False)
    r.workspace = None
    r.flags = {"web": True, "skills": False, "agents": False, "auto_install": False, "code": False, "docs": False}
    return agent, r


def test_new_tools_are_web_tools_known_to_sub_agents_and_the_ui(monkeypatch):
    agent, r = _runner(monkeypatch)
    names = {s["function"]["name"] for s in r.tool_specs()}
    assert {"extract_images", "search_papers", "doi_to_bibtex", "search_images", "view_image"} & names >= {"extract_images", "search_papers", "doi_to_bibtex", "search_images"}
    r.flags["web"] = False
    off = {s["function"]["name"] for s in r.tool_specs()}
    assert not ({"extract_images", "search_papers", "doi_to_bibtex"} & off)
    assert {"extract_images", "search_papers", "doi_to_bibtex"} <= agent.SUB_AGENT_TOOLS
    assert "doi_to_bibtex" in agent.WEB_SAVE_TOOLS  # a sub-agent may only append to a .bib inside its write zone
    from app.routers.assistant import TOOL_NAMES
    assert {"extract_images", "search_papers", "doi_to_bibtex"} <= set(TOOL_NAMES)
    assert "graficador-latex" in agent.BUILTIN_AGENTS


def test_search_images_runs_several_queries_and_reports_the_match(monkeypatch):
    agent, r = _runner(monkeypatch)

    def fake(q, source, limit, lang):
        return [{"title": f"{q} 1.jpg", "url": f"https://x/{q}/1.jpg", "original_url": f"https://x/{q}/1.jpg", "width": 800, "height": 600,
                 "license": "CC BY", "author": "A", "source": "Wikimedia Commons", "page_url": "https://p", "match": "alta", "hits": 2},
                {"title": "dup", "url": "https://x/same.jpg", "match": "baja", "hits": 0, "source": "Openverse"}]
    monkeypatch.setattr(agent.media_fetch, "search_images", fake)
    res = r.tool_search_images("c1", {"queries": ["Pakicetus", "Ambulocetus", "Pakicetus"]})
    assert res["ok"] and "«Pakicetus»" in res["content"] and "«Ambulocetus»" in res["content"]
    assert res["content"].count("https://x/same.jpg") == 1  # the same file is listed once
    assert "coincidencia con la búsqueda: alta" in res["content"]
    assert r.image_meta["https://x/Pakicetus/1.jpg"]["author"] == "A"  # remembered for the credit when it is downloaded
    with pytest.raises(agent.AgentError):
        r.tool_search_images("c2", {})


def test_search_papers_and_bibtex_tools_answer_with_real_fields(monkeypatch):
    agent, r = _runner(monkeypatch)
    monkeypatch.setattr(research_tools, "search_papers", lambda *a, **k: [
        {"title": "Whales", "authors": ["A B"], "year": 2009, "venue": "Evol", "doi": "10.1234/x", "cited_by": 3, "abstract": "ab",
         "oa_url": "", "source": "OpenAlex"}])
    res = r.tool_search_papers("c1", {"query": "whales", "year_from": "2000"})
    assert res["ok"] and "10.1234/x" in res["content"] and res["sources"][0]["url"] == "https://doi.org/10.1234/x"
    monkeypatch.setattr(research_tools, "bibtex", lambda d: "@article{K1, title={T}}")
    res = r.tool_doi_to_bibtex("c2", {"dois": ["10.1234/x"]})
    assert "@article{K1" in res["content"] and res["ok"]
    with pytest.raises(agent.AgentError):
        r.tool_doi_to_bibtex("c3", {"dois": []})
