# Setup journey

The setup console is organized around outcomes rather than raw configuration
sections:

1. **Overview** — required CDM readiness, optional capability status, Verify all,
   and integration output;
2. **Database** — physical connection and logical CDM database entries;
3. **Graph** — CDM-backed search/graph preparation;
4. **Chat Model** — provider and model entries for optional LLM capabilities;
5. **Embeddings** — provider/model, vector store, coverage, population, and index
   work;
6. **Chat** — bounded chat diagnostics;
7. **View Configuration** — redacted shared-stack topology for inspection; and
8. **Runs** — durable local maintenance progress and history.

The required path is **Overview → Configure CDM → Test connections**. Optional
capabilities stay neutral until configured. Every write uses the generic
oa-configurator workflow and shows canonical redacted per-entry changes before
apply. Raw topology is available under View Configuration, not required first-run
knowledge.

See [Initial local setup](../from-scratch.md) for the complete acceptance
journey.
