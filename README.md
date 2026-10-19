# reprooo-app
Reprooo is a web application that transforms unstructured discussions—such as a Slack thread—into a ready-to-use bug report draft, formatted according to a standard QA template.

Here’s how it works: the user inputs the discussion text, and the application masks sensitive information (names, email addresses, service names, and identifiers; keys and passwords are removed entirely). The user reviews the masked text before proceeding. An LLM then generates a structured draft based on the template schema, with completeness checks enforced via code. Finally, the original data—stored exclusively in the session’s memory—is reinserted into the draft. The user can refine the draft (with any missing information highlighted) and create a ticket in Jira Cloud with the click of a button (implemented via a mock for the hackathon; full integration is planned as a next step). Since the template is defined via a configuration file, new formats can be added without requiring additional development.

Measurable impact: reduced time spent on ticket creation and fewer tickets returned for revision. By tracking metrics—such as which fields are edited manually or frequently left blank—the tool’s effectiveness can be quantified.

Future development:
- Slack integration (enabling ticket creation directly from a thread).
- Duplicate detection against an existing database.
- Transitioning from an external LLM provider to a company-approved model.
- A second mode, "Decision Log," which aggregates agreements, assigned owners, deadlines, and pending action items from various sources (Jira, notes, and threads), structured by roles and follow-up tasks.
