from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.hoyesterett import HoyesterettSource
from watchtower.sources.konkurransetilsynet import KonkurransetilsynetSource
from watchtower.sources.regjeringen import RegjeringenSource
from watchtower.sources.stortinget import StortingetSource


class Response:
    def __init__(self, *, text: str = "", content: bytes | None = None) -> None:
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")


class SourceContractTests(unittest.TestCase):
    def config(self, *, kind: str, source_id: str = "source") -> SourceConfig:
        return SourceConfig(
            id=source_id,
            kind=kind,
            label="Example",
            filters=FilterRule(include_any=("example",)),
        )

    def test_regjeringen_rss_is_normalized(self):
        xml = b"""
        <rss><channel><item>
          <title>Example release</title>
          <link>https://example.test/release</link>
          <guid>release-1</guid>
          <pubDate>Wed, 26 Aug 2026 12:00:00 GMT</pubDate>
          <description>Example description</description>
          <category>Example category</category>
        </item></channel></rss>
        """
        source = RegjeringenSource(self.config(kind="regjeringen"))
        source.get = lambda *_args, **_kwargs: Response(content=xml)

        items = source.fetch()

        self.assertEqual(1, len(items))
        self.assertEqual("release-1", items[0].key)
        self.assertEqual("Example release", items[0].title)
        self.assertEqual("https://example.test/release", items[0].url)
        self.assertIn("Example category", items[0].searchable_text())

    def test_stortinget_question_is_normalized(self):
        root = ET.fromstring(
            """
            <root><sporsmal>
              <id>123</id>
              <tittel>Example question</tittel>
              <sendt_dato>2026-08-26</sendt_dato>
              <status>besvart</status>
              <sporsmal_til_minister_tittel>Example minister</sporsmal_til_minister_tittel>
            </sporsmal></root>
            """
        )
        source = StortingetSource(self.config(kind="stortinget"))
        source._xml = lambda *_args, **_kwargs: root

        items = source._fetch_questions()

        self.assertEqual(1, len(items))
        self.assertEqual("sporsmal:123", items[0].key)
        self.assertEqual("Example question", items[0].title)
        self.assertIn("NSporsmalId=123", items[0].url)
        self.assertIn("Example minister", items[0].searchable_text())

    def test_konkurransetilsynet_table_is_normalized(self):
        html = """
        <table><tr>
          <td>26.08.2026</td>
          <td><a href="/example-transaction">Example transaction</a></td>
        </tr></table>
        """
        source = KonkurransetilsynetSource(self.config(kind="konkurransetilsynet"))
        source.get = lambda *_args, **_kwargs: Response(text=html)

        items = source.fetch()

        self.assertEqual(1, len(items))
        self.assertEqual("Example transaction", items[0].title)
        self.assertEqual("26.08.2026", items[0].published)
        self.assertEqual(
            "https://konkurransetilsynet.no/example-transaction",
            items[0].url,
        )

    def test_hoyesterett_decision_is_normalized(self):
        html = """
        <html><body>
          <h1>Example decision</h1>
          <main>26. august 2026 HR-2026-123-A Example facts</main>
        </body></html>
        """
        source = HoyesterettSource(self.config(kind="hoyesterett"))
        source._ranked_decisions = lambda _url: [
            (2026, 123, "HR-2026-123-A", "https://example.test/hr-2026-123-a")
        ]
        source.get = lambda *_args, **_kwargs: Response(text=html)

        items = source.fetch_with_state(None)

        self.assertEqual(1, len(items))
        self.assertEqual("HR-2026-123-A", items[0].key)
        self.assertEqual("Example decision", items[0].title)
        self.assertEqual("26. august 2026", items[0].published)
        self.assertIn("Example facts", items[0].text)


if __name__ == "__main__":
    unittest.main()
