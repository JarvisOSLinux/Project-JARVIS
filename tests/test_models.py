"""Tests for the local GGUF model store (jarvis model pull|list|rm)."""

import httpx
import pytest

from jarvis.config import Config
from jarvis.llm import models


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "MODELS_DIR", str(tmp_path))
    monkeypatch.delenv("HF_TOKEN", raising=False)


def _put(user, repo, name, data=b"GGUF"):
    directory = models.store_dir() / user / repo
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)
    return directory / name


class TestSpec:
    def test_parses_repo_and_quant(self):
        assert models.parse_spec("Qwen/Qwen3-4B-GGUF:Q4_K_M") == (
            "Qwen",
            "Qwen3-4B-GGUF",
            "Q4_K_M",
        )

    def test_quant_is_optional(self):
        assert models.parse_spec("a/b") == ("a", "b", None)

    @pytest.mark.parametrize("bad", ["nomodel", "a/b/c", "a/b:", "../x/y z"])
    def test_rejects_malformed(self, bad):
        with pytest.raises(models.ModelError):
            models.parse_spec(bad)


class TestLocalPath:
    def test_existing_file_is_its_own_path(self, tmp_path):
        gguf = tmp_path / "x.gguf"
        gguf.write_bytes(b"GGUF")
        assert models.local_path(str(gguf)) == gguf

    def test_missing_model_is_none(self):
        assert models.local_path("a/b:Q4_K_M") is None

    def test_quant_selects_matching_file(self):
        _put("a", "b", "b-Q8_0.gguf")
        want = _put("a", "b", "b-Q4_K_M.gguf")
        assert models.local_path("a/b:Q4_K_M") == want

    def test_no_quant_prefers_default_then_any(self):
        _put("a", "b", "b-Q8_0.gguf")
        default = _put("a", "b", "b-Q4_K_M.gguf")
        assert models.local_path("a/b") == default

    def test_mmproj_is_never_the_model(self):
        _put("a", "b", "mmproj-b.gguf")
        assert models.local_path("a/b") is None

    def test_split_model_loads_first_shard(self):
        _put("a", "b", "b-Q4_K_M-00002-of-00002.gguf")
        first = _put("a", "b", "b-Q4_K_M-00001-of-00002.gguf")
        assert models.local_path("a/b:Q4_K_M") == first


class TestChooseFiles:
    SIBLINGS = ["README.md", "b-Q8_0.gguf", "b-Q4_K_M.gguf", "mmproj-b.gguf"]

    def test_default_quant(self):
        assert models._choose_files(self.SIBLINGS, None) == ["b-Q4_K_M.gguf"]

    def test_named_quant(self):
        assert models._choose_files(self.SIBLINGS, "q8_0") == ["b-Q8_0.gguf"]

    def test_unknown_quant_lists_available(self):
        with pytest.raises(models.ModelError, match="Q8_0"):
            models._choose_files(self.SIBLINGS, "Q2_K")

    def test_no_gguf_raises(self):
        with pytest.raises(models.ModelError, match="no .gguf"):
            models._choose_files(["README.md"], None)

    def test_all_shards_chosen(self):
        siblings = [
            "b-Q4_K_M-00001-of-00002.gguf",
            "b-Q4_K_M-00002-of-00002.gguf",
            "b-Q8_0.gguf",
        ]
        assert models._choose_files(siblings, "Q4_K_M") == siblings[:2]


class TestPull:
    def _transport(self, files):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/api/models/a/b":
                return httpx.Response(
                    200, json={"siblings": [{"rfilename": n} for n in files]}
                )
            name = path.rsplit("/", 1)[-1]
            if name in files:
                data = files[name]
                start = 0
                rng = request.headers.get("range")
                if rng:
                    start = int(rng.split("=")[1].rstrip("-"))
                    return httpx.Response(206, content=data[start:])
                return httpx.Response(200, content=data)
            return httpx.Response(404)

        return httpx.MockTransport(handler)

    def _patch(self, monkeypatch, files):
        transport = self._transport(files)
        real_get, real_stream = httpx.get, httpx.stream

        def get(url, **kw):
            with httpx.Client(transport=transport) as c:
                return c.get(url, **kw)

        def stream(method, url, **kw):
            c = httpx.Client(transport=transport)
            return c.stream(method, url, **kw)

        monkeypatch.setattr(httpx, "get", get)
        monkeypatch.setattr(httpx, "stream", stream)
        return real_get, real_stream

    def test_downloads_and_returns_path(self, monkeypatch):
        self._patch(monkeypatch, {"b-Q4_K_M.gguf": b"x" * 100})
        seen = []
        path = models.pull("a/b:Q4_K_M", lambda *a: seen.append(a))
        assert path.read_bytes() == b"x" * 100
        assert seen[-1][1:] == (100, 100)
        assert not list(path.parent.glob("*.part"))

    def test_second_pull_makes_no_requests(self, monkeypatch):
        _put("a", "b", "b-Q4_K_M.gguf")

        def fail(*a, **k):
            raise AssertionError("network used")

        monkeypatch.setattr(httpx, "get", fail)
        assert models.pull("a/b:Q4_K_M").name == "b-Q4_K_M.gguf"

    def test_interrupted_download_resumes(self, monkeypatch):
        data = bytes(range(200))
        self._patch(monkeypatch, {"b-Q4_K_M.gguf": data})
        directory = models.store_dir() / "a" / "b"
        directory.mkdir(parents=True)
        (directory / "b-Q4_K_M.gguf.part").write_bytes(data[:50])
        path = models.pull("a/b:Q4_K_M")
        assert path.read_bytes() == data

    def test_unknown_repo_is_a_clear_error(self, monkeypatch):
        transport = httpx.MockTransport(lambda r: httpx.Response(404))

        def get(url, **kw):
            with httpx.Client(transport=transport) as c:
                return c.get(url, **kw)

        monkeypatch.setattr(httpx, "get", get)
        with pytest.raises(models.ModelError, match="not found on Hugging Face"):
            models.pull("a/b")

    def test_token_sent_when_set(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "tok")
        assert models._headers() == {"Authorization": "Bearer tok"}


class TestListAndRemove:
    def test_list_shows_relative_names_and_sizes(self):
        _put("a", "b", "b-Q4_K_M.gguf", b"12345")
        assert models.list_models() == [("a/b/b-Q4_K_M.gguf", 5)]

    def test_remove_by_quant_keeps_others(self):
        _put("a", "b", "b-Q4_K_M.gguf")
        keep = _put("a", "b", "b-Q8_0.gguf")
        assert models.remove("a/b:Q4_K_M") == 1
        assert keep.exists()

    def test_remove_last_file_drops_directory(self):
        _put("a", "b", "b-Q4_K_M.gguf")
        models.remove("a/b")
        assert not (models.store_dir() / "a" / "b").exists()

    def test_remove_missing_raises(self):
        with pytest.raises(models.ModelError, match="not pulled"):
            models.remove("a/b")
