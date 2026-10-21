# Working with prompts

Tell the agent what you want to achieve, what you already know, and what is still undecided. Choose a prompt workflow that fits the task and ask for more guidance when needed.

Prompt packs contain reusable instructions for an agent. They can help you develop a study question, review source fields, or make a mapping decision. Groundworkers supplies the tools and guidance; your agent or application runs the conversation and keeps track of decisions. If you need to resume later, ask it to save those decisions and the draft.

## How the parts fit together

| Part | Question it addresses | Example |
|---|---|---|
| Workflow guidance | What needs to happen next? | Establish the study question, gather missing parameters, and prepare a draft for review. |
| User questions (elicitation) | What does the user mean or want to decide? | Ask which comparison you intend and clarify your answer. |
| Domain knowledge | What rules and evidence apply here? | Apply the relevant OMOP mapping conventions and source-specific context. |
| Tools | What can be looked up or checked? | Retrieve concept candidates or check a structured document. |

You can use these parts together or separately. An agent can clarify a comparison on its own, or ask the same question as part of a study-design workflow.

Once the agent understands your answer, the workflow still needs to check that it meets the task's requirements before continuing.

## Start with a goal and relevant context

1. **Describe the outcome.** Say whether you want an explanation, a draft study question, mapping recommendations, or a document for review.
2. **Supply what you already know.** Include relevant source information, constraints, and decisions you have made. Identify anything still undecided.
3. **Choose a pack.** Use your client's prompt list, or ask the agent to find a pack that fits the task.
4. **Review the result.** Check that it reflects your intent, look at the supporting evidence, and note anything still uncertain.

For example:

> Help me turn this study goal into a research question. Use the relevant study
> workflow, reuse the decisions I have already supplied, and ask one focused
> question at a time when required information is missing. Keep undecided
> parameters unresolved and show me the draft for review.

The packs you can use depend on your installation and client. Knowledge packs supply guidance such as mapping rules; workflow packs help organise the task. Finding a knowledge pack does not start a workflow. See the [knowledge tools](../tools/knowledge.md) for finding and reading knowledge packs. Workflow packs are available through the optional [OHDSI Prompt Registry](https://AustralianCancerDataNetwork.github.io/ohdsi-prompt-registry/).

## Keep track of answers and open questions

A useful question says what needs deciding and gives you enough context to answer. The differences between options should be clear. If none fits, you should be able to explain what you mean.

Your client may show clickable options or a numbered question in chat. In either case, the agent should record your response and wait for an answer before doing work that requires it. A default selection, silence, or closing a dialog is not an answer.

| Your response | What the agent should preserve |
|---|---|
| An answer | Your intended choice or explanation, with clarification where needed. |
| “I will decide later” | A deferred decision, with dependent work still pending where the answer is required. |
| “I do not want to answer” | A declined question; the workflow determines whether it can continue without that information. |
| An unclear response or no answer | An open question, with no answer assumed. |

Work can often continue while a question is open. An optional preference can wait; a required study parameter may hold up the final result while still allowing a draft.

You can change an earlier answer. Ask the agent to check which decisions and drafts need updating. For example, changing the comparison may affect the research question while leaving unrelated work intact.

## Ask for more explanation when you need it

The main instructions should contain the rules needed for routine work. Separate references can explain unfamiliar concepts, give more examples, or cover unusual cases.

The agent can start with those instructions and read more about a particular issue when needed. Required rules belong in the main instructions, so optional background can be left unread without missing a requirement.

You can ask:

> Explain why this question is needed and show one relevant example. Use the
> supporting guidance for this issue rather than loading the whole reference
> collection.

Separate files help only if the client reads them as needed. Everything the agent reads takes up space in its working context, so ask for the explanation relevant to your question.

## Save results for review and reuse

Use the conversation to agree what the result means. A structured document makes it easier to save, check, and pass to another tool or team. Ask for a readable summary so you can review it without working directly with JSON.

Passing validation means the document meets the structural checks applied to it. You still need to review its clinical or scientific content and decide whether to approve it. Keep missing decisions visible in the draft; the agent should not fill them with assumptions to make the document look complete.

## Tell the agent how you prefer to work

You can ask for one question at a time, short or detailed explanations, a preferred language, or a summary before the evidence. For batch review, ask the agent to collect open questions in a review document so it need not interrupt for every row.

These preferences do not change the domain rules. Local or source-specific guidance applies to its intended setting. If guidance conflicts, ask the agent to explain the conflict and what evidence would help resolve it.

Your agent or application controls how questions appear, whether answers are saved, and whether required steps are enforced. Prompt instructions alone cannot guarantee that behaviour. Before relying on a result, ask the agent to show which guidance it used, what you decided, and what is still open.
