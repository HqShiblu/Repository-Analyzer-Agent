
# DECISIONS.md

## Design Decisions

**1.** Remove trailing slash of repository.

**2.** Save the edited url, question and the embedding of the question.

**3.** Use local model for embedding. The model should be all-MiniLM-L6-v2 and the model name should be taken from .env file.

**4.** First, search with embedding if the question has already been asked and answered.

**5.** If the question is already known to the LLM then answer from it's own knowledge base.

**6.** If the user is asking for summary first search for README.md and relevant files that can be useful depending on the repository type or framework.

**7.** If none of the above is true then run the actual procedure.

**8.** First the ai agent should bring the directory tree with github api and then do the rest.

**9.** Instead of loading whole code, first send the method names to the llm to analyze based on the dependency. To detect the methods first get the regular expression for that specific programming language. Then analyze that specific method whichever the llm thinks might be useful.

**10.** After generating the answer the agent should save the answer so that it can be served later semantically.

**11.** The appliaction should use django, django REST framework and Postgresql database.

**12.** The LLM should be OpenAI compatible.

**13.** Database host, port, username, password, LLM url, LLM name, LLM api key i.e. all the credentials should be read from .env file.

**14.** Set a variable to run the maximum agent loop so that the agent doesn't run forever. The variable should be read from the .env file.

**15.** The cmd should show the tool it is calling and files that are being read.

**16.** Also print the number of tokens being used in each LLM call. At the end, print total number of tokens used for each api call.

**17.** There should be four models- Repository, ResearchSession, Finding, ToolCallLog.

**Repository** tracks repositories that are being searched. There is one row per repository, url is unique.

**ResearchSession** holds the question, its embedding, the final answer, source category, completion timestamps, and token usage. Each new question creates a new session row linked to it's Repository via a foreign key repository_id.

**ToolCallLog** logs which tool ran, its inputs, and a truncated summary of the output, the database stores this under a foreign key to ResearchSession with session_id. One ResearchSession can have multiple ToolCallLog rows.

**Finding** keeps the track when the agent concluded something meaningful about a particular file. One ResearchSession may have many Finding rows over the lifetime of one question.

## Database Schema Rationale

**At scale:** The `question_embedding` vector index (`ivfflat`, cosine ops) will need
tuning as the table grows. The `Finding` and `ToolCallLog` tables will grow fast on
active systems and would benefit from partitioning by `session_id` or archiving old
sessions.

## What I Would Do Differently With More Time

- Add async task execution (Celery) so the API returns a session ID immediately and
  the agent runs in the background, with a polling or webhook endpoint for results.
- Rate-limit and cache GitHub API calls within a session to avoid redundant fetches
  when the agent calls `read_file` on the same path twice.
- Add an admin dashboard to browse sessions, findings and tool call logs without needing to query the database directly.

---

## Known Limitations

- The agent runs synchronously in the request/response cycle. Long sessions on large
  repositories may cause the HTTP request to time out.
- The semantic cache threshold (0.92 cosine similarity) is a fixed value. Questions
  that are semantically close but meaningfully different (asking about two different
  parts of the same system) could theoretically collide, though in practice 0.92 is tight enough to avoid this.