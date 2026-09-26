"""A deterministic, offline model provider for development and tests (refused in staging/prod).

It answers each stage from the structured payload the router sends -- the same data a real model
sees -- so the full Flow, including budgets and validation, runs without network access or an API
key. Tests can queue overrides per output type to exercise repair loops and failures.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel

from analytics_orchestrator.application.services.ports import (
    OutputT,
    ProviderError,
    ProviderResponse,
)
from analytics_orchestrator.domain.value_objects.agent_outputs import (
    AnalyticsRequest,
    GeneratedSql,
    PlanFilter,
    PlanMeasure,
    QueryPlan,
    ResultInsight,
    SemanticResolution,
    TimeRange,
)
from platform_contracts import ChartEncodings, ChartOptions, ChartSpec, Encoding

Override = (
    BaseModel | ProviderError | ProviderResponse[Any] | Callable[[Mapping[str, Any]], BaseModel]
)
_UNSAFE_TEXT = re.compile(r"[<>{}`\x00-\x1f]")
#: Date bucketing in MySQL, which has no date_trunc (week/quarter fall back to month here).
_MYSQL_BUCKETS = {"day": "%Y-%m-%d", "month": "%Y-%m-01", "year": "%Y-01-01"}
_SMALL_TALK = re.compile(r"\b(hi|hello|hey|thanks|thank you|help|what can you do)\b")
_QUARTERS = {"q1": (1, 3), "q2": (4, 6), "q3": (7, 9), "q4": (10, 12)}


class ScriptedProvider:
    name = "scripted"

    def __init__(self, latency_seconds: float = 0.0) -> None:
        self.latency_seconds = latency_seconds
        self.overrides: dict[type[BaseModel], list[Override]] = defaultdict(list)
        self.calls: list[str] = []
        self.users: list[str] = []

    def queue(self, output_type: type[BaseModel], *outputs: Override) -> None:
        self.overrides[output_type].extend(outputs)

    async def generate(
        self,
        *,
        model: str,
        system: str,
        user: str,
        payload: Mapping[str, Any],
        output_type: type[OutputT],
        max_tokens: int,  # noqa: ARG002 - scripted answers are always short
    ) -> ProviderResponse[OutputT]:
        if self.latency_seconds:
            await asyncio.sleep(self.latency_seconds)
        self.calls.append(output_type.__name__)
        self.users.append(user)
        input_tokens = (len(system) + len(user)) // 4 + 1
        queued = self.overrides.get(output_type)
        if queued:
            item = queued.pop(0)
            if isinstance(item, ProviderError):
                raise item
            if isinstance(item, ProviderResponse):
                return item  # type: ignore[return-value]
            output = item(payload) if callable(item) and not isinstance(item, BaseModel) else item
        else:
            output = self._answer(output_type, payload)
        body = output.model_dump_json()
        return ProviderResponse(
            output=output,
            input_tokens=input_tokens,
            output_tokens=len(body) // 4 + 1,
            model=f"scripted/{model}",
        )  # type: ignore[arg-type]

    def _answer(self, output_type: type[BaseModel], payload: Mapping[str, Any]) -> BaseModel:
        if output_type is AnalyticsRequest:
            return self._request(payload)
        if output_type is QueryPlan:
            return self._plan(payload)
        if output_type is GeneratedSql:
            return self._sql(payload)
        if output_type is ChartSpec:
            return self._chart(payload)
        if output_type is SemanticResolution:
            return self._semantic(payload)
        if output_type is ResultInsight:
            return self._insight(payload)
        raise ProviderError()

    @staticmethod
    def _request(payload: Mapping[str, Any]) -> AnalyticsRequest:
        text = str(payload.get("user_request", ""))
        lowered = text.lower()
        today_text = str(payload.get("today") or dt.datetime.now(dt.UTC).date().isoformat())
        today = dt.date.fromisoformat(today_text)
        time_range = TimeRange(grain="month" if "month" in lowered else None)
        for quarter, (first, last) in _QUARTERS.items():
            if re.search(rf"\b{quarter}\b", lowered):
                end = dt.date(today.year, last, 1).replace(day=28) + dt.timedelta(days=4)
                time_range = TimeRange(
                    start=dt.date(today.year, first, 1),
                    end=end - dt.timedelta(days=end.day),
                    grain=time_range.grain or "month",
                )
        analytic = any(
            word in lowered
            for word in ("revenue", "sales", "chart", "dashboard", "trend", "orders", "show")
        )
        if not analytic and _SMALL_TALK.search(lowered):
            return AnalyticsRequest(
                intent="conversation",
                title="Greeting",
                reply=(
                    "Hello! I can build charts and answer questions about your connected data. "
                    "Try asking me to show monthly revenue as a chart."
                ),
            )
        title = _UNSAFE_TEXT.sub("", text).strip()[:120] or "Analysis"
        return AnalyticsRequest(
            intent="visualization" if analytic else "unsupported",
            title=title,
            metrics=["revenue"] if ("revenue" in lowered or "sales" in lowered) else [],
            dimensions=["month"] if "month" in lowered else [],
            time_range=time_range,
        )

    @staticmethod
    def _semantic(payload: Mapping[str, Any]) -> SemanticResolution:
        """Lexical: a definition matches when its name or a synonym appears in the request."""
        text = " ".join(re.findall(r"[a-z0-9]+", str(payload.get("user_request", "")).lower()))

        def matches(item: Mapping[str, Any]) -> bool:
            words = [str(item["name"]), *item.get("synonyms", [])]
            return any(re.search(rf"\b{re.escape(w.lower())}\b", text) for w in words)

        return SemanticResolution(
            metric_ids=[m["id"] for m in payload.get("metrics", []) if matches(m)][:5],
            dimension_ids=[d["id"] for d in payload.get("dimensions", []) if matches(d)][:3],
        )

    @staticmethod
    def _insight(payload: Mapping[str, Any]) -> ResultInsight:
        stats = payload.get("result_stats", {})
        title = _UNSAFE_TEXT.sub("", str(payload.get("request", {}).get("title") or "Result"))
        rows = int(stats.get("row_count", 0))
        measure = next(
            (
                c
                for c in stats.get("columns", [])
                if c["type"] == "quantitative" and c.get("sum") is not None
            ),
            None,
        )
        if measure is None:
            headline = f"{title}: {rows} row{'s' if rows != 1 else ''}"
        else:
            headline = (
                f"{title}: {measure['field']} ranged from {measure['min']:,.2f} to "
                f"{measure['max']:,.2f} across {rows} rows"
            )
        return ResultInsight(headline=headline[:200])

    @staticmethod
    def _semantic_plan(payload: Mapping[str, Any]) -> QueryPlan | None:
        semantic = payload.get("semantic") or {}
        metrics = semantic.get("metrics") or []
        if not metrics:
            return None
        measures = [PlanMeasure.model_validate(m["measure"]) for m in metrics]
        table = measures[0].column.rsplit(".", 1)[0]
        catalog = {f"{t['schema_name']}.{t['table_name']}": t for t in payload.get("catalog", [])}
        columns = catalog.get(table, {}).get("columns", [])
        temporal = next(
            (c["name"] for c in columns if any(t in c["data_type"] for t in ("date", "time"))),
            None,
        )
        request = payload.get("request", {})
        time_range = request.get("time_range") or {}
        filters = []
        if temporal and time_range.get("start") and time_range.get("end"):
            filters.append(
                PlanFilter(
                    column=f"{table}.{temporal}",
                    operator="between",
                    values=[time_range["start"], time_range["end"]],
                )
            )
        return QueryPlan(
            tables=[table],
            measures=[m for m in measures if m.column.startswith(f"{table}.")],
            dimensions=[d["column"] for d in semantic.get("dimensions", [])],
            time_column=f"{table}.{temporal}" if temporal else None,
            time_grain=(time_range.get("grain") or "month") if temporal else None,
            filters=filters,
        )

    @staticmethod
    def _plan(payload: Mapping[str, Any]) -> QueryPlan:
        planned = ScriptedProvider._semantic_plan(payload)
        if planned is not None:
            return planned
        request = payload.get("request", {})
        for table in payload.get("catalog", []):
            columns = table.get("columns", [])
            temporal = next(
                (c["name"] for c in columns if any(t in c["data_type"] for t in ("date", "time"))),
                None,
            )
            numeric = next(
                (c["name"] for c in columns if c["name"] in ("amount", "revenue", "total")),
                None,
            )
            if temporal and numeric:
                qualified = f"{table['schema_name']}.{table['table_name']}"
                time_range = request.get("time_range") or {}
                filters = []
                if time_range.get("start") and time_range.get("end"):
                    filters.append(
                        PlanFilter(
                            column=f"{qualified}.{temporal}",
                            operator="between",
                            values=[time_range["start"], time_range["end"]],
                        )
                    )
                return QueryPlan(
                    tables=[qualified],
                    measures=[
                        PlanMeasure(
                            column=f"{qualified}.{numeric}", aggregation="sum", alias="revenue"
                        )
                    ],
                    time_column=f"{qualified}.{temporal}",
                    time_grain=time_range.get("grain") or "month",
                    filters=filters,
                )
        raise ProviderError()

    @staticmethod
    def _sql(payload: Mapping[str, Any]) -> GeneratedSql:
        plan = QueryPlan.model_validate(payload["plan"])
        table = plan.tables[0]
        measure = plan.measures[0]
        grain = plan.time_grain or "month"
        assert plan.time_column is not None
        time_column = plan.time_column.split(".")[-1]
        where = ""
        for plan_filter in plan.filters:
            if plan_filter.operator == "between":
                start, end = (dt.date.fromisoformat(v) for v in plan_filter.values)
                where = (
                    f" WHERE t.{time_column} >= DATE '{start}'"
                    f" AND t.{time_column} < DATE '{end + dt.timedelta(days=1)}'"
                )
        # Built only from catalog identifiers the plan was checked against and ISO dates;
        # the result is validated by query-gateway like any model output.
        column = f"t.{measure.column.split('.')[-1]}"
        aggregate = (
            f"count(DISTINCT {column})"
            if measure.aggregation == "count_distinct"
            else f"{measure.aggregation}({column})"
        )
        bucket = (
            f"CAST(DATE_FORMAT(t.{time_column}, '{_MYSQL_BUCKETS.get(grain, '%Y-%m-01')}') AS DATE)"
            if payload.get("dialect") == "mysql"
            else f"date_trunc('{grain}', t.{time_column})"
        )
        select_list = f"{bucket} AS {grain}, {aggregate} AS {measure.alias}"
        return GeneratedSql(
            sql=f"SELECT {select_list} FROM {table} AS t{where} GROUP BY 1 ORDER BY 1"  # noqa: S608
        )

    @staticmethod
    def _chart(payload: Mapping[str, Any]) -> ChartSpec:
        fields = payload.get("result_schema", [])
        x = next((f for f in fields if f["type"] == "temporal"), fields[0] if fields else None)
        y = next((f for f in fields if f["type"] == "quantitative"), None)
        request = payload.get("request", {})
        title = _UNSAFE_TEXT.sub("", str(request.get("title") or "Result"))[:120]
        if x is None or y is None:
            return ChartSpec(type="table", options=ChartOptions(title=title))
        return ChartSpec(
            type=request.get("chart_preference") or "line",
            encoding=ChartEncodings(
                x=Encoding(field=x["field"], type=x["type"]),
                y=Encoding(field=y["field"], type="quantitative"),
            ),
            options=ChartOptions(title=title, legend=True),
        )


def dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)
