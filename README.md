# qler

Background jobs without Redis, with first-class debugging.

qler is an async-first background job queue for Python, built on SQLite via [sqler](https://github.com/gabu/sqler). It prioritizes debuggability, zero infrastructure, and integration with the -ler ecosystem.

## Status

**Pre-implementation.** The [technical specification](SPEC.md) is complete. Implementation is blocked on [sqler gaps](SQLER_GAPS.md) that must be resolved first.

## Why qler?

- **Zero infrastructure** — SQLite is the only dependency. No Redis, no RabbitMQ.
- **Debuggability first** — Full attempt history, structured failure kinds, correlation IDs. "Why did this fail?" has a complete answer.
- **Async-native** — Built for `asyncio` from the ground up.
- **-ler ecosystem** — Integrates with sqler (storage), logler (logs), and procler (process management).

## Quick Look (Aspirational API)

```python
from qler import Queue, task

queue = Queue("jobs.db")

@task(queue, max_retries=3)
async def send_email(to: str, subject: str, body: str):
    await smtp.send(to, subject, body)

# Enqueue
job = await send_email.enqueue(to="user@example.com", subject="Hi", body="Hello")

# Run worker
await queue.run_worker(queues=["default"], concurrency=4)
```

## Documentation

- [Technical Specification](SPEC.md) — Full API design, data models, and behavior
- [North Star](NORTH_STAR.md) — Vision, philosophy, and non-negotiable principles
- [sqler Gaps](SQLER_GAPS.md) — Blocking dependencies on sqler
- [Brainstorm](BRAINSTORM.md) — Design exploration and competitive analysis

## License

MIT
