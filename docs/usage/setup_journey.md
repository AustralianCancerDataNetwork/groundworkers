# Setup console

The setup console is organized by the capability being configured:

1. **Overview** — CDM readiness, optional capabilities, and integration output;
2. **Database** — physical connections and logical CDM database entries;
3. **Graph** — CDM-backed search and graph preparation;
4. **Performance** — graph, trigram, and embedding-index readiness;
5. **LLM provider** — provider and model entries for language features;
6. **Embeddings** — vector-store setup, coverage, and population;
7. **Chat** — checks for the selected chat model;
8. **Configuration** — redacted stack configuration; and
9. **Runs** — progress and history for durable maintenance work.

For a minimal local setup, use **Overview → Configure CDM → Test connections**. Optional capabilities can be configured later. Configuration changes show the redacted entries that will be written before they are applied.

See [Initial local setup](../from-scratch.md) for a complete example.
