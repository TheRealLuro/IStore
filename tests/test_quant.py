import importlib


def _reload():
    import backend.vision.quant as q
    return importlib.reload(q)


def test_rewriter_4bit_on_by_default(monkeypatch):
    monkeypatch.delenv("LLM_REWRITER_4BIT", raising=False)
    q = _reload()
    cfg = q.rewriter_quant_config()
    assert cfg is not None
    assert getattr(cfg, "load_in_4bit", False) is True
    assert cfg.bnb_4bit_quant_type == "nf4"


def test_rewriter_4bit_can_be_disabled(monkeypatch):
    monkeypatch.setenv("LLM_REWRITER_4BIT", "0")
    q = _reload()
    assert q.rewriter_quant_config() is None


def test_nllb_8bit_off_by_default(monkeypatch):
    monkeypatch.delenv("NLLB_8BIT", raising=False)
    q = _reload()
    assert q.nllb_quant_config() is None


def test_nllb_8bit_on_when_set(monkeypatch):
    monkeypatch.setenv("NLLB_8BIT", "1")
    q = _reload()
    cfg = q.nllb_quant_config()
    assert cfg is not None and getattr(cfg, "load_in_8bit", False) is True
