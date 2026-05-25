from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData, Table, and_, create_engine, func, or_, select, text
from sqlalchemy.engine import Engine

from groundworkers.base.results import DetailResult, ListResult, SearchHit, SearchResult
from groundworkers.config import FullTextConfig, SqlResourceConfig


class SQLResource:
    def __init__(self, engine: Engine, resource_id: str, config: SqlResourceConfig) -> None:
        self.engine = engine
        self.resource_id = resource_id
        self.config = config
        self._table: Table | None = None

    @property
    def table(self) -> Table:
        if self._table is None:
            metadata = MetaData(schema=self.config.db_schema)
            self._table = Table(self.config.table, metadata, autoload_with=self.engine)
        return self._table

    def _project(self, row: Any) -> dict[str, Any]:
        mapping = dict(row._mapping)
        if self.config.display_fields:
            return {field: mapping.get(field) for field in self.config.display_fields}
        return mapping

    def _validate_filters(self, filters: dict[str, Any] | None) -> dict[str, Any]:
        if not filters:
            return {}
        invalid = sorted(set(filters) - set(self.config.allowed_filter_fields or []))
        if invalid:
            raise ValueError(f"Unsupported filter fields for {self.resource_id}: {', '.join(invalid)}")
        return filters

    def get(self, key: Any) -> DetailResult:
        statement = select(self.table).where(self.table.c[self.config.primary_key] == key).limit(1)
        with self.engine.connect() as connection:
            row = connection.execute(statement).first()
        return DetailResult(resource_id=self.resource_id, item=self._project(row) if row else None)

    def list(self, *, limit: int = 20, offset: int = 0, filters: dict[str, Any] | None = None) -> ListResult:
        safe_limit = max(1, min(limit, 100))
        safe_offset = max(0, offset)
        validated = self._validate_filters(filters)
        predicates = [self.table.c[column] == value for column, value in validated.items()]
        statement = select(self.table)
        count_stmt = select(func.count()).select_from(self.table)
        if predicates:
            predicate = and_(*predicates)
            statement = statement.where(predicate)
            count_stmt = count_stmt.where(predicate)
        statement = statement.limit(safe_limit).offset(safe_offset)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).all()
            total = connection.execute(count_stmt).scalar_one()
        return ListResult(
            resource_id=self.resource_id,
            items=[self._project(row) for row in rows],
            total=int(total),
            limit=safe_limit,
            offset=safe_offset,
        )


class SQLTextSearchResource(SQLResource):
    def __init__(self, engine: Engine, resource_id: str, config: SqlResourceConfig, fulltext: FullTextConfig) -> None:
        super().__init__(engine, resource_id, config)
        self.fulltext = fulltext
        self._search_table: Table | None = None

    @property
    def search_table(self) -> Table:
        if self._search_table is None:
            metadata = MetaData(schema=self.fulltext.db_schema)
            self._search_table = Table(self.fulltext.table, metadata, autoload_with=self.engine)
        return self._search_table

    def search(self, query: str, *, limit: int = 10) -> SearchResult:
        safe_limit = max(1, min(limit, 50))
        dialect = self.engine.dialect.name
        table = self.search_table
        if dialect == "postgresql" and self.fulltext.vector_column:
            where_clause = text(f"{self.fulltext.vector_column} @@ plainto_tsquery(:query)")
            statement = select(table).where(where_clause).limit(safe_limit).params(query=query)
        else:
            predicates = [table.c[field].ilike(f"%{query}%") for field in self.fulltext.search_fields if field in table.c]
            if not predicates:
                raise ValueError(f"No search fields configured for {self.resource_id}")
            statement = select(table).where(or_(*predicates)).limit(safe_limit)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).all()
        items = [
            SearchHit(
                id=str(row._mapping.get(self.config.primary_key, index)),
                score=1.0,
                payload=dict(row._mapping),
            )
            for index, row in enumerate(rows)
        ]
        return SearchResult(resource_id=self.resource_id, query=query, items=items, limit=safe_limit)


def build_engine(url: str) -> Engine:
    return create_engine(url, future=True)
