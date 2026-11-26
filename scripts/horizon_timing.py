"""日报分段计时。作为入口运行时沿用上游 Horizon CLI。"""
from contextlib import contextmanager
from functools import wraps
from hashlib import sha256
from inspect import iscoroutinefunction
import syslog
from time import monotonic


@contextmanager
def timed(stage, fields=""):
    prefix = f"horizon_timing stage={stage} {fields}".rstrip()
    start = monotonic()
    syslog.syslog(syslog.LOG_INFO, f"{prefix} status=start")
    try:
        yield
    except BaseException as exc:
        syslog.syslog(syslog.LOG_WARNING,
                      f"{prefix} status=error type={type(exc).__name__} elapsed_s={monotonic() - start:.3f}")
        raise
    else:
        syslog.syslog(syslog.LOG_INFO, f"{prefix} status=ok elapsed_s={monotonic() - start:.3f}")


def trace_method(owner, name, stage, fields=None):
    original = getattr(owner, name, None)
    # 上游升级后即使某个计时点改名，仍使用原来的 CLI 生成日报。
    if not iscoroutinefunction(original):
        syslog.syslog(syslog.LOG_WARNING, f"horizon_timing stage={stage} status=unavailable")
        return

    @wraps(original)
    async def measured(self, *args, **kwargs):
        with timed(stage, fields(self, *args, **kwargs) if fields else ""):
            return await original(self, *args, **kwargs)

    setattr(owner, name, measured)


def item_fields(_, item, *args, **kwargs):
    return f"item={sha256(item.id.encode()).hexdigest()[:12]}"


def main():
    from src.main import main as horizon
    from src.orchestrator import ContentEnricher, DailySummarizer, HorizonOrchestrator

    trace_method(HorizonOrchestrator, "run", "generate")
    trace_method(HorizonOrchestrator, "fetch_all_sources", "fetch")
    trace_method(HorizonOrchestrator, "analyze_items", "score")
    trace_method(HorizonOrchestrator, "select_digest_items", "select")
    trace_method(HorizonOrchestrator, "enrich_items", "details",
                 lambda app, items: f"items={len(items)} concurrency={app.config.ai.enrichment_concurrency}")
    # 这里已拿到上游信号量，单条耗时不包含等候空位的时间。
    trace_method(ContentEnricher, "_enrich_item", "detail", item_fields)
    trace_method(ContentEnricher, "_plan_and_execute_tools", "detail_research", item_fields)
    trace_method(ContentEnricher, "_generate_artifact", "detail_write", item_fields)
    trace_method(DailySummarizer, "generate_summary", "render")
    return horizon()


if __name__ == "__main__":
    raise SystemExit(main())
