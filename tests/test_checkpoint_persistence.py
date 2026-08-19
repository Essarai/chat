from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from langgraph.checkpoint.sqlite import SqliteSaver

from app.agents.controller import build_prepare_graph, init_state


class CheckpointPersistenceTests(unittest.TestCase):
    def test_graph_state_survives_reopen(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.db"
            config = {"configurable": {"thread_id": "ZDXBNXB:test-thread"}}

            conn = sqlite3.connect(path, check_same_thread=False)
            graph = build_prepare_graph(checkpointer=SqliteSaver(conn))
            graph.invoke(
                init_state("近三年发文量前十的作者", journal_id="ZDXBNXB"),
                config,
            )
            conn.close()

            reopened_conn = sqlite3.connect(path, check_same_thread=False)
            reopened = build_prepare_graph(checkpointer=SqliteSaver(reopened_conn))
            snapshot = reopened.get_state(config)
            self.assertEqual(snapshot.values["query_plan"]["task"], "top_authors")
            self.assertEqual(len(snapshot.values["result_set"]["items"]), 10)
            reopened_conn.close()


if __name__ == "__main__":
    unittest.main()
