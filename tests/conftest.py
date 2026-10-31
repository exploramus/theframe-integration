import asyncio
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from fake_tv import FakeFrameTV

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def theframe(tmp_path_factory):
    """the add-on module; it creates a controller at import, so give it options first"""
    options = tmp_path_factory.mktemp("import") / "options.json"
    options.write_text(json.dumps({"tv_ip": "192.0.2.1"}))
    os.environ["THEFRAME_OPTIONS"] = str(options)
    spec = importlib.util.spec_from_file_location("theframe", ROOT / "theframe_controller" / "app" / "theframe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_options(tmp_path, **options):
    images = tmp_path / "images"
    images.mkdir(exist_ok=True)
    path = tmp_path / "options.json"
    path.write_text(json.dumps({
        "tv_ip": "192.0.2.1",
        "token_file": str(tmp_path / "token.txt"),
        "json_path": str(tmp_path / "uploaded_files.json"),
        "image_path": str(images) + "/",
        **options,
    }))
    return path


@pytest.fixture
def addon(theframe, tmp_path):
    """add-on controller wired to a fake TV; check() runs one monitor check and waits for what it started"""
    controller = theframe.TheFrameController(options_file=str(write_options(tmp_path)))
    tv = FakeFrameTV()
    controller.create_tv = lambda: tv

    async def no_wait(files):       # the add-on waits up to 10 s for files to finish copying
        pass

    controller.wait_for_files = no_wait

    async def check_and_settle():
        await controller.monitor.check()
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.gather(*pending)

    return SimpleNamespace(
        controller=controller,
        tv=tv,
        images=tmp_path / "images",
        json_path=tmp_path / "uploaded_files.json",
        client=TestClient(controller.app),
        check=lambda: asyncio.run(check_and_settle()),
    )
