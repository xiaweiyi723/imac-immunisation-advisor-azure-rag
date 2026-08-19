"""
Azure AI Foundry Agent backend module.
Handles interaction with the Azure AI Foundry Agent.
"""

import os
import logging
import re
import json
import urllib.error
import urllib.request
from typing import Optional, Dict, List, Any, Iterable

from dotenv import load_dotenv
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Configuration from environment variables
PROJECT_ENDPOINT = os.getenv("PROJECT_ENDPOINT")
AGENT_NAME = os.getenv("AGENT_NAME")
AGENT_VERSION = os.getenv("AGENT_VERSION")
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")
AZURE_SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX", "imac-guidance-poc")
EVALUATION_CASES_PATH = os.getenv("EVALUATION_CASES_PATH", r"C:\Users\惠普\Desktop\evaluation_cases.txt")
MAX_DISPLAY_SOURCES = 5
MAX_SNIPPETS_PER_FILE = 3


class AzureAgentClient:
    """Azure AI Foundry Agent client."""

    def __init__(self):
        """Initialize the Azure Agent client."""
        if not PROJECT_ENDPOINT:
            raise ValueError("PROJECT_ENDPOINT environment variable is not set")
        if not AGENT_NAME:
            raise ValueError("AGENT_NAME environment variable is not set")
        if not AGENT_VERSION:
            raise ValueError("AGENT_VERSION environment variable is not set")

        self.project_endpoint = PROJECT_ENDPOINT
        self.agent_name = AGENT_NAME
        self.agent_version = AGENT_VERSION

        try:
            # Uses Azure CLI authentication. Run `az login` before starting locally.
            credential = DefaultAzureCredential()
            self.credential = credential

            self.project_client = AIProjectClient(
                endpoint=self.project_endpoint,
                credential=credential,
            )

            # OpenAI client used to call the Agent Reference via Responses API.
            self.openai_client = self.project_client.get_openai_client()

            logger.info("Azure AI Project Client initialized successfully")

        except Exception as e:
            logger.error(f"Azure AI Project Client initialization failed: {str(e)}")
            raise

    def call_agent(
        self,
        user_message: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Call the Azure Agent and return an answer.

        Args:
            user_message: The user's question.
            conversation_history: Conversation history used for multi-turn context.

        Returns:
            A dictionary with answer text, source data, and success status.
        """
        try:
            search_sources = self.query_azure_search(user_message, clinical_only=True)
            related_call_sources = self.query_local_evaluation_cases(user_message, top=2)
            grounded_prompt = user_message
            if search_sources:
                context = "\n\n".join(
                    f"[Source {i}] {src.get('file_name')} | {src.get('file_path')}\n{src.get('quote')}"
                    for i, src in enumerate(search_sources, start=1)
                )
                grounded_prompt = (
                    "Use the verified Azure AI Search context below when it is relevant. "
                    "Do not invent citations. If the context and attached files do not support the answer, say that no verified source was found.\n\n"
                    "If the clinical question is vague, such as a sick infant or child, do not assume the illness. "
                    "State what information is missing and only provide general guidance that is directly supported by the sources.\n\n"
                    f"Verified Azure AI Search context:\n{context}\n\nAdvisor question:\n{user_message}"
                )

            input_messages: List[Dict[str, str]] = []

            # Add previous messages for multi-turn context.
            if conversation_history:
                for msg in conversation_history:
                    role_text = msg.get("role", "")
                    content = msg.get("content", "")

                    if not content:
                        continue

                    role = "user" if "User" in role_text or role_text == "user" else "assistant"
                    input_messages.append({
                        "role": role,
                        "content": content,
                    })

            # Add the current user message.
            input_messages.append({"role": "user", "content": grounded_prompt})

            logger.info(f"Calling Agent: {self.agent_name} (v{self.agent_version})")
            logger.info(f"Message count: {len(input_messages)}")

            response = self.openai_client.responses.create(
                input=input_messages,
                include=["file_search_call.results"],
                extra_body={
                    "agent_reference": {
                        "name": self.agent_name,
                        "version": self.agent_version,
                        "type": "agent_reference",
                    }
                },
            )

            logger.info("Agent response received successfully")

            # Extract answer text.
            assistant_message = getattr(response, "output_text", None)
            if not assistant_message:
                assistant_message = str(response)

            # Responses API sources often live in output/content/annotations or
            # file_search_call.results instead of response.context.sources.
            sources = self.extract_sources_from_response_object(response)

            if search_sources:
                sources.extend(search_sources)
            if related_call_sources:
                sources.extend(related_call_sources)

            sources = self.rank_sources_for_display(sources)
            assistant_message = self.clean_answer_text(
                assistant_message,
                remove_source_section=bool(sources),
            )

            logger.info(f"Answer prepared successfully. Source count: {len(sources)}")

            return {
                "response": assistant_message,
                "sources": sources,
                "success": True,
            }

        except Exception as e:
            logger.error(f"Agent call failed: {str(e)}", exc_info=True)
            return {
                "response": None,
                "sources": [],
                "success": False,
                "error": str(e),
            }

    def query_azure_search(self, query: str, top: int = 5, clinical_only: bool = False) -> List[Dict[str, str]]:
        """
        Query the real Azure AI Search PoC knowledge index.

        The index contains official NZ immunisation sources and the local
        anonymised evaluation_cases.txt question set. These are structured
        sources, unlike citations that may appear inside model text.
        """
        if not AZURE_SEARCH_ENDPOINT or not AZURE_SEARCH_INDEX:
            return []
        try:
            token = self.credential.get_token("https://search.azure.com/.default").token
            expanded_query = self.expand_search_query(query)
            body = {
                "search": expanded_query,
                "top": max(top * 4, 12),
                "queryType": "semantic",
                "semanticConfiguration": "default",
                "searchMode": "any",
                "select": "title,source_file,source_type,url,chunk_id,content",
            }
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                f"{AZURE_SEARCH_ENDPOINT}/indexes/{AZURE_SEARCH_INDEX}/docs/search?api-version=2023-11-01",
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            candidate_results = []
            for row in payload.get("value", []) or []:
                source = {
                    "file_name": str(row.get("source_file") or row.get("title") or "Azure AI Search source"),
                    "file_id": f"{AZURE_SEARCH_INDEX}:{row.get('chunk_id', '')}",
                    "file_path": str(row.get("url") or ""),
                    "quote": str(row.get("content") or ""),
                    "score": str(row.get("@search.score") or ""),
                    "type": str(row.get("source_type") or "azure_ai_search"),
                }
                if self.is_navigation_noise(source):
                    continue
                if clinical_only and self.is_query_context_mismatch(query, source):
                    continue
                if clinical_only and not self.source_matches_query_intent(query, source):
                    continue
                if clinical_only and not self.is_clinical_authority_source(source):
                    continue
                candidate_results.append(source)

            candidate_results.sort(
                key=lambda source: (
                    self.source_authority_rank(source),
                    self.source_relevance_rank(source, expanded_query),
                    self.safe_float(source.get("score")),
                ),
                reverse=True,
            )

            results = []
            for source in candidate_results[:top]:
                source["quote"] = self.make_relevant_excerpt(source.get("quote", ""), expanded_query)
                results.append(source)
            return results
        except Exception as exc:
            logger.warning("Azure AI Search query failed: %s", exc)
            return []

    def query_local_evaluation_cases(self, query: str, top: int = 2) -> List[Dict[str, str]]:
        """
        Return related anonymised call examples from evaluation_cases.txt.

        These examples support use-case realism and evaluation coverage. They
        are deliberately labelled as call evidence, not clinical authority.
        """
        if not EVALUATION_CASES_PATH or not os.path.exists(EVALUATION_CASES_PATH):
            return []

        try:
            with open(EVALUATION_CASES_PATH, "r", encoding="utf-8", errors="ignore") as case_file:
                text = case_file.read()
        except OSError as exc:
            logger.warning("Could not read evaluation cases: %s", exc)
            return []

        blocks = [
            block.strip()
            for block in re.split(r"\n={20,}\n", text)
            if block.strip()
        ]
        query_terms = self.evaluation_case_terms(query)
        scored_blocks = []
        for index, block in enumerate(blocks, start=1):
            block_lower = block.lower()
            score = 0
            for term, weight in query_terms:
                if term in block_lower:
                    score += weight
            if score <= 0:
                continue
            scored_blocks.append((score, index, block))

        scored_blocks.sort(key=lambda item: item[0], reverse=True)

        sources = []
        for score, index, block in scored_blocks[:top]:
            excerpt = self.make_relevant_excerpt(block, self.expand_search_query(query), max_chars=850)
            sources.append({
                "file_name": "evaluation_cases.txt",
                "file_id": f"evaluation_cases:call-{index}",
                "file_path": EVALUATION_CASES_PATH,
                "quote": excerpt,
                "score": str(score),
                "type": "related_anonymised_call_example",
            })
        return sources

    def evaluation_case_terms(self, query: str) -> List[tuple[str, int]]:
        query_text = query.lower()
        terms: List[tuple[str, int]] = [
            ("vaccine", 1),
            ("vaccination", 1),
            ("immunisation", 1),
        ]

        if self.query_has_topic(query, "influenza"):
            terms.extend([
                ("influenza", 12),
                ("flu vaccine", 12),
                ("flu", 8),
            ])
        if self.query_has_topic(query, "child"):
            terms.extend([
                ("6 months", 10),
                ("between 6 months", 12),
                ("under 6 months", 8),
                ("children", 8),
                ("child", 6),
                ("infant", 8),
                ("baby", 8),
                ("4 years", 4),
                ("5 months", 5),
                ("6 year", 3),
            ])
        if self.query_has_topic(query, "illness"):
            terms.extend([
                ("fever", 8),
                ("temperature", 7),
                ("sick", 6),
                ("unwell", 6),
                ("illness", 6),
                ("vomit", 4),
            ])

        # If the query is mostly Chinese, keep the intent-specific English
        # terms above and add any literal ASCII words present in the query.
        for term in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", query_text):
            terms.append((term, 2))
        return terms

    def expand_search_query(self, query: str) -> str:
        """Add English clinical retrieval terms for Chinese advisor prompts."""
        lower_query = query.lower()
        terms = [query]

        if any(term in query for term in ("婴儿", "宝宝", "孩子", "儿童", "小孩")) or any(
            term in lower_query for term in ("infant", "baby", "child", "paediatric", "pediatric")
        ):
            terms.append("infant child paediatric immunisation vaccine schedule contraindications precautions")

        if any(term in query for term in ("六个月", "6个月", "6 月")) or any(
            term in lower_query for term in ("6 month", "six month", "6-month")
        ):
            terms.append("6 months of age infant immunisation vaccine schedule")

        if any(term in query for term in ("有病", "生病", "发烧", "发热", "不舒服", "疾病")) or any(
            term in lower_query for term in ("sick", "ill", "illness", "fever", "unwell", "acute illness")
        ):
            terms.append(
                "mildly unwell temperature 38 conditions that are not contraindications "
                "acute illness fever defer vaccination not contraindication contraindications precautions"
            )

        if any(term in query for term in ("流感", "流感疫苗")) or any(
            term in lower_query for term in ("flu", "influenza")
        ):
            terms.append("influenza vaccine children 6 months older contraindications")

        if self.query_has_topic(query, "influenza"):
            terms.append("influenza vaccine children 6 months older contraindications precautions")

        return " ".join(dict.fromkeys(" ".join(terms).split()))

    def query_has_topic(self, query: str, topic: str) -> bool:
        query_text = query.lower()
        aliases = {
            "influenza": ("influenza", "flu", "\u6d41\u611f", "\u6d41\u611f\u75ab\u82d7"),
            "child": (
                "infant",
                "baby",
                "child",
                "children",
                "paediatric",
                "pediatric",
                "6 month",
                "six month",
                "6-month",
                "\u5a74\u513f",
                "\u5b9d\u5b9d",
                "\u5b69\u5b50",
                "\u513f\u7ae5",
                "\u5c0f\u5b69",
                "\u516d\u4e2a\u6708",
                "6\u4e2a\u6708",
            ),
            "illness": (
                "sick",
                "ill",
                "illness",
                "fever",
                "unwell",
                "acute illness",
                "\u6709\u75c5",
                "\u751f\u75c5",
                "\u53d1\u70e7",
                "\u53d1\u70ed",
                "\u4e0d\u8212\u670d",
                "\u75be\u75c5",
            ),
        }
        return any(alias in query_text or alias in query for alias in aliases.get(topic, ()))

    def is_clinical_authority_source(self, source: Dict[str, str]) -> bool:
        source_type = (source.get("type") or "").lower()
        file_name = (source.get("file_name") or "").lower()
        path = (source.get("file_path") or "").lower()

        non_authority_types = {
            "project_brief",
            "project_use_case",
            "anonymised_call_question_set",
            "evaluation_case",
        }
        if source_type in non_authority_types:
            return False

        if any(name in file_name for name in ("hackathon", "adviser_agent", "evaluation_cases")):
            return False

        return any(
            marker in " ".join([source_type, file_name, path])
            for marker in (
                "immunisation-handbook",
                "immunisation handbook",
                "official_guidance",
                "health.govt.nz",
                "immune.org.nz",
                "medsafe",
                "pharmac",
            )
        )

    def is_navigation_noise(self, source: Dict[str, str]) -> bool:
        content = (source.get("quote") or "").lower()
        noise_markers = (
            "skip to main content",
            "feedback or complaints",
            "women's pelvic",
            "women\u2019s pelvic",
            "service support and eligibility",
            "tamariki",
            "return to eligibility",
            "health and disability services",
        )
        if any(marker in content for marker in noise_markers):
            clinical_markers = (
                "contraindication",
                "precaution",
                "immunisation",
                "vaccination",
                "vaccine",
                "influenza",
                "fever",
            )
            return not any(marker in content for marker in clinical_markers)
        return False

    def is_query_context_mismatch(self, query: str, source: Dict[str, str]) -> bool:
        query_text = query.lower()
        content = (source.get("quote") or "").lower()

        if any(marker in content for marker in ("conditions that are not contraindications", "table 2.3")):
            return False

        disease_markers = {
            "covid": ("covid", "sars-cov-2", "mrna-cv"),
            "pneumococcal": ("pneumococcal", "pcv13", "pcv"),
            "meningococcal": ("meningococcal", "menquadfi", "nimenrix"),
            "tdap": ("tdap", "pertussis"),
            "influenza": ("influenza", "flu"),
            "mpox": ("mpox",),
        }
        chinese_query_aliases = {
            "influenza": ("流感",),
            "covid": ("新冠", "冠状"),
            "pneumococcal": ("肺炎球菌",),
            "meningococcal": ("脑膜炎", "脑膜炎球菌"),
            "tdap": ("百日咳", "破伤风", "白喉"),
        }

        for topic, markers in disease_markers.items():
            source_mentions_topic = any(marker in content for marker in markers)
            query_mentions_topic = any(marker in query_text for marker in markers)
            query_mentions_topic = query_mentions_topic or any(
                alias in query for alias in chinese_query_aliases.get(topic, ())
            )
            if source_mentions_topic and not query_mentions_topic:
                return True

        return False

    def source_matches_query_intent(self, query: str, source: Dict[str, str]) -> bool:
        content = (source.get("quote") or "").lower()
        file_name = (source.get("file_name") or "").lower()
        combined = f"{file_name} {content}"

        asks_influenza = self.query_has_topic(query, "influenza")
        asks_child = self.query_has_topic(query, "child")
        asks_illness = self.query_has_topic(query, "illness")

        has_general_contraindication = any(
            marker in content
            for marker in (
                "conditions that are not contraindications",
                "not contraindications to immunisation",
                "mildly unwell",
                "temperature \u226438",
                "temperature <=38",
                "temperature \u2264 38",
                "contraindications",
                "precautions",
            )
        )

        if asks_influenza:
            has_influenza_vaccine = any(
                marker in combined
                for marker in (
                    "influenza vaccine",
                    "influenza vaccines",
                    "influenza vaccination",
                    "flu vaccine",
                    "flu vaccination",
                    "medsafe influenza",
                )
            )
            if not has_influenza_vaccine and not has_general_contraindication:
                return False

            if asks_child:
                has_child_context = any(
                    marker in content
                    for marker in (
                        "6 months of age",
                        "aged 6 months",
                        "from 6 months",
                        "6 months and older",
                        "children",
                        "child",
                        "infant",
                        "infants",
                        "paediatric",
                        "pediatric",
                    )
                )
                adult_only = any(
                    marker in content
                    for marker in (
                        "65 years",
                        "65 years and older",
                        "older adults",
                        "adults 65",
                        "high-risk adults",
                    )
                )
                if adult_only and not has_child_context:
                    return False
                if not has_child_context and not has_general_contraindication:
                    return False

        if asks_illness and not asks_influenza:
            has_illness_context = any(
                marker in content
                for marker in (
                    "mildly unwell",
                    "temperature \u226438",
                    "temperature <=38",
                    "temperature \u2264 38",
                    "acute illness",
                    "fever",
                    "contraindications",
                    "precautions",
                    "not contraindications",
                )
            )
            if not has_illness_context:
                return False

        return True

    def source_authority_rank(self, source: Dict[str, str]) -> int:
        file_name = (source.get("file_name") or "").lower()
        path = (source.get("file_path") or "").lower()
        source_type = (source.get("type") or "").lower()

        if "immunisation-handbook" in file_name or "immunisation handbook" in file_name:
            return 50
        if "official_guidance" in source_type:
            return 45
        if "immune.org.nz" in path:
            return 35
        if "medsafe" in " ".join([file_name, path, source_type]):
            return 30
        if "pharmac" in " ".join([file_name, path, source_type]):
            return 25
        return 10

    def source_relevance_rank(self, source: Dict[str, str], expanded_query: str) -> int:
        content = (source.get("quote") or "").lower()
        query = expanded_query.lower()
        rank = 0
        age_query = any(marker in query for marker in ("6 months", "6 months of age", "infant"))
        if age_query:
            if any(marker in content for marker in ("6 months of age", "aged 6 months", "from 6 months", "infant", "infants")):
                rank += 10
            if "6 months after" in content and not any(marker in content for marker in ("6 months of age", "aged 6 months", "infant", "infants")):
                rank -= 12

        for marker in ("child", "children", "young children"):
            if marker in query and marker in content:
                rank += 4
        for marker in ("contraindication", "contraindications", "precaution", "acute illness", "fever", "defer"):
            if marker in query and marker in content:
                rank += 8
        for marker in ("not contraindications", "not a contraindication", "should be immunised in the usual way"):
            if marker in content:
                rank += 10
        for marker in ("influenza", "vaccine", "immunisation", "vaccination"):
            if marker in query and marker in content:
                rank += 3
        disease_specific_markers = (
            "covid",
            "sars-cov-2",
            "influenza",
            "measles",
            "pneumococcal",
            "meningococcal",
            "rotavirus",
            "tdap",
            "mpox",
        )
        if not any(marker in query for marker in disease_specific_markers):
            if any(marker in content for marker in disease_specific_markers):
                if "conditions that are not contraindications" not in content:
                    rank -= 20
        return rank

    def make_relevant_excerpt(self, content: str, expanded_query: str, max_chars: int = 900) -> str:
        if len(content) <= max_chars:
            return content

        content_lower = content.lower()
        query_lower = expanded_query.lower()
        preferred_markers = [
            "conditions that are not contraindications",
            "mildly unwell",
            "6 months of age",
            "aged 6 months",
            "from 6 months",
            "6 months and older",
            "children",
            "infants",
            "not a contraindication",
            "not contraindications",
            "contraindications",
            "precautions",
            "fever",
            "infant",
            "children",
        ]
        if ("influenza" in query_lower or "flu" in query_lower) and not any(
            marker in query_lower for marker in ("child", "children", "infant", "6 months")
        ):
            preferred_markers.insert(0, "influenza")

        marker_positions = [
            content_lower.find(marker)
            for marker in preferred_markers
            if content_lower.find(marker) >= 0
        ]
        if marker_positions:
            start = max(min(marker_positions) - 120, 0)
        else:
            start = 0

        excerpt = content[start:start + max_chars].strip()
        if start > 0:
            excerpt = "..." + excerpt
        if start + max_chars < len(content):
            excerpt = excerpt.rstrip() + "..."
        return excerpt

    def safe_float(self, value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def extract_sources_from_response_object(self, response: Any) -> List[Dict[str, str]]:
        """
        Extract sources from the Azure/OpenAI Responses API response object.

        File Search references often appear in nested annotations,
        file_citation, or file_search_call.results fields. SDK object
        shapes can vary by version, so this first converts the response
        to plain dictionaries and then walks the nested structure.
        """
        response_data = self._to_plain_data(response)
        sources: List[Dict[str, str]] = []

        # Keep compatibility with older code paths that used context.sources.
        context = response_data.get("context") if isinstance(response_data, dict) else None
        if isinstance(context, dict):
            for source in context.get("sources", []) or []:
                self._append_source(sources, source, "context")

        for node in self._walk_dicts(response_data):
            node_type = str(node.get("type") or "").lower()

            if "citation" in node_type or "annotation" in node_type:
                self._append_source(sources, node, node_type)

            if "file_search" in node_type:
                for result in node.get("results", []) or []:
                    self._append_source(sources, result, "file_search")

            # Some SDK versions flatten file details without a helpful type.
            if any(key in node for key in ("file_id", "filename", "file_name")):
                self._append_source(sources, node, node_type or "file")

            for nested_key in ("file_citation", "file_path", "citation", "source"):
                nested = node.get(nested_key)
                if nested:
                    self._append_source(sources, nested, nested_key, parent=node)

        return self._dedupe_sources(sources)

    def _append_source(
        self,
        sources: List[Dict[str, str]],
        raw_source: Any,
        source_type: str,
        parent: Optional[Dict[str, Any]] = None,
    ) -> None:
        source = self._to_plain_data(raw_source)
        parent = parent or {}

        if not isinstance(source, dict):
            return

        file_name = (
            source.get("filename")
            or source.get("file_name")
            or source.get("title")
            or source.get("name")
            or parent.get("filename")
            or parent.get("file_name")
            or parent.get("title")
            or parent.get("name")
        )
        file_id = source.get("file_id") or source.get("id") or parent.get("file_id")
        url = source.get("url") or source.get("path") or parent.get("url") or parent.get("path")
        quote = (
            source.get("quote")
            or source.get("text")
            or source.get("content")
            or parent.get("text")
        )
        score = source.get("score") or parent.get("score")

        if not any([file_name, file_id, url, quote]):
            return

        sources.append({
            "file_name": str(file_name or file_id or url or "Unknown"),
            "file_id": str(file_id or ""),
            "file_path": str(url or ""),
            "quote": str(quote or ""),
            "score": str(score or ""),
            "type": source_type or "source",
        })

    def rank_sources_for_display(
        self,
        sources: Iterable[Dict[str, str]],
        limit: int = MAX_DISPLAY_SOURCES,
    ) -> List[Dict[str, str]]:
        ranked_sources = self._dedupe_sources(sources)

        def score_value(source: Dict[str, str]) -> float:
            try:
                return float(source.get("score") or 0)
            except ValueError:
                return 0

        official_sources = [
            source
            for source in ranked_sources
            if source.get("type") != "related_anonymised_call_example"
        ]
        call_sources = [
            source
            for source in ranked_sources
            if source.get("type") == "related_anonymised_call_example"
        ]
        official_sources.sort(key=score_value, reverse=True)
        call_sources.sort(key=score_value, reverse=True)

        selected = []
        snippets_by_file: Dict[str, int] = {}
        for source in official_sources:
            file_key = source.get("file_id") or source.get("file_name") or "unknown"
            current_count = snippets_by_file.get(file_key, 0)
            if current_count >= MAX_SNIPPETS_PER_FILE:
                continue

            selected.append(source)
            snippets_by_file[file_key] = current_count + 1

            if len(selected) >= min(3, limit):
                break

        for source in call_sources[:2]:
            if len(selected) >= limit:
                break
            selected.append(source)

        return selected

    def clean_answer_text(self, response_text: str, remove_source_section: bool = False) -> str:
        if not response_text:
            return ""

        cleaned = response_text.strip()
        cleaned = re.sub(r"^\s*Answer:\s*", "", cleaned, flags=re.IGNORECASE)

        if remove_source_section:
            cleaned = re.sub(
                r"\n+\s*(Source|Sources|Reference|References):\s*[\s\S]*$",
                "",
                cleaned,
                flags=re.IGNORECASE,
            ).strip()

        return cleaned

    def _dedupe_sources(self, sources: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
        unique_sources = []
        seen = set()

        for source in sources:
            key = (
                source.get("file_name", ""),
                source.get("file_id", ""),
                source.get("file_path", ""),
                source.get("quote", "")[:120],
            )
            if key not in seen:
                unique_sources.append(source)
                seen.add(key)

        return unique_sources

    def _to_plain_data(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value

        if isinstance(value, dict):
            return {key: self._to_plain_data(item) for key, item in value.items()}

        if isinstance(value, (list, tuple, set)):
            return [self._to_plain_data(item) for item in value]

        if hasattr(value, "model_dump"):
            return self._to_plain_data(value.model_dump())

        if hasattr(value, "to_dict"):
            return self._to_plain_data(value.to_dict())

        if hasattr(value, "__dict__"):
            return {
                key: self._to_plain_data(item)
                for key, item in vars(value).items()
                if not key.startswith("_")
            }

        return str(value)

    def _walk_dicts(self, value: Any) -> Iterable[Dict[str, Any]]:
        if isinstance(value, dict):
            yield value
            for item in value.values():
                yield from self._walk_dicts(item)
        elif isinstance(value, list):
            for item in value:
                yield from self._walk_dicts(item)

    def extract_sources_from_response(self, response_text: str) -> List[Dict[str, str]]:
        """
        Extract source information from answer text.

        Args:
            response_text: The Agent answer text.

        Returns:
            A list of source dictionaries.
        """
        sources: List[Dict[str, str]] = []

        if not response_text:
            return sources

        # Match [citation: filename.pdf]
        citations = re.findall(r"\[citation:\s*([^\]]+)\]", response_text, flags=re.IGNORECASE)
        for citation in citations:
            sources.append({
                "file_name": citation.strip(),
                "type": "citation",
            })

        # Match Source: xxx or Sources: xxx
        source_blocks = re.findall(
            r"(?:Source|Sources):\s*([^\n]+)",
            response_text,
            flags=re.IGNORECASE,
        )
        for src in source_blocks:
            cleaned = src.strip().strip("-").strip()
            if cleaned:
                sources.append({
                    "file_name": cleaned,
                    "type": "source",
                })

        return self._dedupe_sources(sources)
