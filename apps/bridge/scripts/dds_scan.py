"""Diagnostic: scan for visible DDS participants/topics across candidate domains.

Run with `CYCLONEDDS_HOME=...` set, no peer config (default multicast).
"""
import os
import time

# Use defaults (multicast). Drop any prior URI.
os.environ.pop("CYCLONEDDS_URI", None)

from cyclonedds.domain import DomainParticipant  # noqa: E402
from cyclonedds.builtin import (  # noqa: E402
    BuiltinDataReader,
    BuiltinTopicDcpsParticipant,
    BuiltinTopicDcpsTopic,
)

for domain in [0, 1, 25, 42, 50]:
    try:
        dp = DomainParticipant(domain)
    except Exception as exc:
        print(f"Domain {domain}: failed to create participant ({exc})")
        continue
    time.sleep(1.5)
    preader = BuiltinDataReader(dp, BuiltinTopicDcpsParticipant)
    treader = BuiltinDataReader(dp, BuiltinTopicDcpsTopic)
    parts = list(preader.take(N=50))
    topics = list(treader.take(N=200))
    print(f"Domain {domain}: {len(parts)} participants, {len(topics)} topics")
    for t in topics[:15]:
        print(f"  topic: {getattr(t, 'topic_name', '?')} type: {getattr(t, 'type_name', '?')}")
