"""Evidence packages connecting generated answers back through every stage."""


def evidence_package(*, query, data_records=(), information_records=(),
                     knowledge_records=()):
    return {
        "format_version": 1,
        "query": query,
        "data_records": list(data_records),
        "information_records": list(information_records),
        "knowledge_records": list(knowledge_records),
        "generation_status": "not_generated",
    }

