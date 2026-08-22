from __future__ import annotations

import json
import shutil
import sys
import unittest
import zipfile
from pathlib import Path


TASK_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TASK_DIR))

from coordinator import ResearchWritingCoordinator  # noqa: E402
from requirement_loader import infer_topic, load_requirement_file  # noqa: E402


class CapturingBackend:
    name = "capturing-real-compatible"
    model = "capturing-model"
    last_retries = 0

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(
        self, *, system_prompt: str, user_prompt: str, fallback: str
    ) -> str:
        del system_prompt
        self.prompts.append(user_prompt)
        return fallback


class RequirementImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.runtime = TASK_DIR / "tests" / "_requirements_runtime"
        if self.runtime.exists():
            shutil.rmtree(self.runtime)
        self.runtime.mkdir(parents=True)

    async def asyncTearDown(self) -> None:
        if self.runtime.exists():
            shutil.rmtree(self.runtime)

    async def test_text_and_docx_loading_and_topic_inference(self) -> None:
        markdown = self.runtime / "brief.md"
        markdown.write_text(
            "# 项目名称：面向海洋监测的边缘智能系统\n\n要求支持三类传感器。",
            encoding="utf-8",
        )
        text = load_requirement_file(markdown)
        self.assertIn("三类传感器", text)
        self.assertEqual(
            infer_topic(text, default="default"), "面向海洋监测的边缘智能系统"
        )

        document = self.runtime / "brief.docx"
        xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>项目题目：智慧农业协作平台</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>周期：24个月</w:t></w:r></w:p></w:body></w:document>"
        )
        with zipfile.ZipFile(document, "w") as archive:
            archive.writestr("word/document.xml", xml)
        docx_text = load_requirement_file(document)
        self.assertIn("智慧农业协作平台", docx_text)
        self.assertIn("24个月", docx_text)

    async def test_imported_brief_reaches_agents_and_final_proposal(self) -> None:
        backend = CapturingBackend()
        requirements = (
            "项目名称：面向海洋牧场的智能监测平台\n"
            "必须覆盖水质、鱼群和设备状态，周期24个月，输出软件原型和技术报告。"
        )
        coordinator = ResearchWritingCoordinator(
            backend=backend,
            task_dir=self.runtime,
            console=False,
            topic="面向海洋牧场的智能监测平台",
            requirements_text=requirements,
            requirements_source="brief.md",
        )

        summary = await coordinator.run()

        self.assertTrue(summary["requirements_imported"])
        self.assertEqual(summary["topic"], "面向海洋牧场的智能监测平台")
        self.assertEqual(summary["requirements_source"], "brief.md")
        self.assertTrue(backend.prompts)
        for prompt in backend.prompts:
            self.assertIn("水质、鱼群和设备状态", prompt)

        proposal = (self.runtime / "outputs" / "final_proposal.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("# 面向海洋牧场的智能监测平台", proposal)
        self.assertIn("水质、鱼群和设备状态", proposal)
        archived = (self.runtime / "inputs" / "imported_requirements.txt").read_text(
            encoding="utf-8"
        )
        self.assertEqual(archived, requirements)
        meta = json.loads(
            (self.runtime / "inputs" / "requirements_meta.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(meta["source"], "brief.md")
        self.assertEqual(len(meta["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
