"""Static UI contract checks for the restored quick workflow."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src/dwg-layer-standardizer/ui"


class QuickWorkflowTests(unittest.TestCase):
    def test_quick_workflow_is_visible_and_wired(self):
        index = (UI / "index.html").read_text(encoding="utf-8")
        app = (UI / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="quick-flow-btn"', index)
        self.assertIn('id="quick-flow-note"', index)
        self.assertIn("$('#quick-flow-btn').addEventListener('click',quickFlow)", app)

    def test_quick_workflow_keeps_human_approval_and_verified_copy(self):
        app = (UI / "app.js").read_text(encoding="utf-8")
        self.assertIn("reason:'human_approved_suggestion'", app)
        self.assertIn("核准建議並另存驗證", app)
        self.assertIn("if(pendingAfter===0&&canConvert())await convert()", app)
        self.assertIn("原始圖面不會被覆寫", app)

    def test_verified_dwg_prompts_for_a_user_selected_save_location(self):
        app = (UI / "app.js").read_text(encoding="utf-8")
        self.assertIn("window.showSaveFilePicker", app)
        self.assertIn("選擇位置並儲存 DWG", app)
        self.assertIn("createWritable()", app)

    def test_completed_backend_result_recovers_a_lost_success_response(self):
        app = (UI / "app.js").read_text(encoding="utf-8")
        api = (ROOT / "src/dwg-layer-standardizer/cadlayer/app.py").read_text(encoding="utf-8")
        self.assertIn("'/api/jobs/'+job+'/result'", app)
        self.assertIn('@app.get("/api/jobs/{job_id}/result")', api)
        self.assertIn("result = completed_result(folder)", api)
        self.assertIn("new URLSearchParams(location.search).get('result_job')", app)
        self.assertIn("showVerifiedResult(result", app)


if __name__ == "__main__":
    unittest.main()
