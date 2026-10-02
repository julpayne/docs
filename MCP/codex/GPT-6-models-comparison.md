# GPT-6 models: effort and price comparison (2 Oct 2026)

The GPT-6 models available in this Codex session are **GPT-6 Astra**, **GPT-6 Sol**, and **GPT-6 Luna**. OpenAI's current API pricing lists the middle model as **GPT-6.1 Sol**. The prices below are standard API token rates, included as a relative cost comparison; they are not Codex subscription charges.

## Performance and API price

- **GPT-6 Astra** — OpenAI's most capable model for demanding reasoning, coding, research, and document work. **$10 per million input tokens; $50 per million output tokens.** [Model details](https://developers.openai.com/api/docs/models/gpt-6-astra)
- **GPT-6.1 Sol** — described by OpenAI as offering near-Astra performance for complex work at lower cost. **$2 per million input tokens; $10 per million output tokens** (one-fifth Astra's token price). [Model details](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
- **GPT-6 Luna** — OpenAI's efficient option for focused, high-volume tasks. **$0.10 per million input tokens; $0.50 per million output tokens** (one-hundredth Astra's token price). [Model details](https://developers.openai.com/api/docs/models/gpt-6-luna)

For a rough illustration, one million input tokens plus one million output tokens costs **$60 with Astra, $12 with Sol, or $0.60 with Luna** at standard API rates. Actual quality depends on the task; OpenAI's model guidance describes the relative roles but does not provide one benchmark score that captures performance across all workloads. See the [model comparison](https://developers.openai.com/api/docs/models/compare) and [API pricing](https://developers.openai.com/api/docs/pricing).

## Reasoning effort

The effort levels are **low, medium, high, xhigh, and max**. **GPT-6 Sol and Luna also support `none`; Astra does not.** Lower effort generally favors speed and fewer reasoning tokens. Higher effort can help on harder problems, at the cost of added latency and token use. Start at **medium** for a balanced setting; use xhigh or max when the additional reasoning helps on your workload. See OpenAI's [reasoning guidance](https://developers.openai.com/api/docs/guides/reasoning).

### How effort affects cost

Effort does **not** change a model's price per token or apply a fixed multiplier. It can change the total request cost because higher effort may use more reasoning tokens. OpenAI bills those hidden reasoning tokens at the model's **output-token rate**, along with visible output tokens. The number used depends on the task; an effort level does not guarantee a specific token count. See [how reasoning tokens are billed](https://developers.openai.com/api/docs/guides/reasoning#how-reasoning-works) and [API pricing](https://developers.openai.com/api/docs/pricing).

For example, if a higher effort setting uses **10,000 more reasoning tokens**, that adds about **$0.50 with Astra, $0.10 with Sol, or $0.005 with Luna** at the standard output rates above. Input-token charges are unchanged unless the prompt itself changes. To compare effort settings accurately, compare actual output and reasoning token usage for representative tasks; the API reports reasoning-token counts in the response usage details.

## Suggested starting points

- **Routine, well-scoped edits:** Luna at low.
- **Day-to-day coding:** Sol at medium.
- **Difficult debugging or broad, high-stakes work:** Astra at medium or high.
