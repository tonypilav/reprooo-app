# Reprooo

**AI-powered requirements refinement — from raw context to development-ready Jira tickets.**

Reprooo is a web application that transforms unstructured business requests, discussions, and technical context into clear, structured, and testable requirements.

Instead of simply rewriting task descriptions, Reprooo identifies missing information, ambiguities, contradictions, and potential edge cases before they become blockers during development or QA.

The goal is to improve task readiness, reduce clarification cycles, and enable QA engineers to participate earlier in the development lifecycle.

## The Problem

Software development teams frequently receive tasks with incomplete or ambiguous requirements.

Critical details — such as expected behavior, access permissions, error handling, dependencies, and acceptance criteria — are often discovered only after development has started or when a task reaches testing.

This results in additional discussions, rework, delayed testing, and inconsistent expectations between business stakeholders, developers, and QA engineers.

Reprooo addresses this problem by introducing an AI-assisted requirements refinement process before or during the early stages of development.

## How It Works

The target workflow consists of six stages:

1. **Provide context.** The user enters an initial business request, task description, or discussion copied from Slack. Supporting technical documentation may be attached where supported.
2. **Mask sensitive information.** The application detects and masks configured categories of sensitive data. The user reviews the masked content before AI processing. Credentials and secrets must never be restored into generated tickets.
3. **Generate an initial draft.** An LLM analyzes the context and produces a structured Jira task, highlighting missing requirements, ambiguities, contradictions, and potential edge cases.
4. **Refine requirements iteratively.** The user answers clarification questions, edits the draft, and requests another analysis. Each iteration updates only relevant sections while preserving previously approved requirements.
5. **Finalize the task.** The user reviews the resulting requirements, acceptance criteria, and unresolved questions before deciding whether the task is ready.
6. **Create a Jira ticket.** The finalized task is submitted through a Jira adapter. The hackathon MVP uses a mock implementation; real Jira Cloud integration is planned for future development.

The user controls the number of refinement iterations and makes the final decision about task readiness.

## Key Capabilities

- **Context-aware analysis:** identifies potential gaps based on the type of task and the supplied information.
- **Structured requirements:** generates business context, functional requirements, acceptance criteria, constraints, and relevant edge cases.
- **Clarification questions:** distinguishes blocking questions from recommended and optional clarifications.
- **Incremental refinement:** updates existing drafts without unnecessarily rewriting previously approved requirements.
- **Sensitive-data masking:** configurable masking rules with a user-facing preview.
- **Template-driven generation:** task structure is defined through configuration rather than hardcoded into the UI.
- **Human-in-the-loop workflow:** the AI suggests improvements but does not invent or independently approve business decisions.
- **Jira-ready output:** produces a structured task suitable for review and submission.

## Technology Stack

| Layer                   | Technology                                   |
| ----------------------- | -------------------------------------------- |
| Frontend                | React, JavaScript, Mantine, Vite             |
| Backend                 | Python, FastAPI, Pydantic                    |
| LLM integration         | OpenAI API                                   |
| Orchestration           | Modular Python orchestration layer           |
| Sensitive-data handling | Configurable masking rules                   |
| Session management      | In-memory session store with TTL             |
| Task templates          | Configuration-driven schemas                 |
| Jira integration        | Adapter pattern, mock implementation for MVP |

## Architecture

The application uses a modular architecture separating the user interface, orchestration, sensitive-data processing, LLM integration, requirements validation, session management, and Jira integration.

The intended processing flow is:

`User Context → Masking → LLM Draft Generation → Completeness Analysis → Iterative Refinement → Final Review → Jira Adapter`

Draft generation and completeness analysis are logically separate responsibilities.

Refinement should be incremental: previously approved requirements must remain unchanged unless the user explicitly authorizes modifications.

Sensitive data must be handled before external LLM requests. Supporting documentation is subject to the same security considerations as user-provided context.

## Hackathon MVP

The MVP focuses on demonstrating the complete requirements refinement workflow:

- Enter unstructured context.
- Configure and review sensitive-data masking.
- Generate an initial structured task using an LLM.
- Identify missing requirements and generate clarification questions.
- Refine the draft through multiple iterations.
- Preserve previously approved requirements.
- Review and finalize the resulting task.
- Simulate Jira ticket creation.
- Reset the workflow after successful submission.

### Current Implementation Status

The repository already contains the frontend workflow, configurable masking, session handling, a template-driven draft editor, an orchestration layer, and a mock Jira adapter.

However, the transition from the original bug-report prototype to the requirements refinement workflow is still in progress.

In particular, real LLM generation is not yet connected to the active workflow, iterative clarification is not yet implemented, and the existing bug-report template must be replaced.

The current application should therefore be considered a **working prototype under active development**, not a completed implementation of all capabilities described above.

## Expected Impact

Reprooo aims to improve the quality of requirements before development and testing.

Potential effectiveness metrics include:

- Reduction in clarification questions raised during QA.
- Reduction in tasks returned for requirements clarification.
- Time required to prepare development-ready tasks.
- Number and severity of missing requirements identified before development.
- Number of refinement iterations per task.
- Frequency of changes to acceptance criteria after development begins.

These are proposed evaluation metrics, not measured outcomes of the current prototype.

## Future Development

- **Knowledge base:** reusable specifications, documentation, and team-specific requirements.
- **Team and service context:** automatically retrieve relevant documentation based on the selected team or service.
- **Repository integration:** use relevant technical specifications and code context when analyzing requirements.
- **Specification generation:** preserve technical specifications alongside pull requests.
- **Real Jira Cloud integration:** create and update tasks directly.
- **Slack integration:** import discussions without manual copying.
- **Organizational learning:** reuse previously identified requirement gaps, clarification patterns, and team conventions.
- **Company-approved LLM providers:** support deployment using approved internal or external models.
- **Analytics:** measure requirements quality and refinement effectiveness.

## Project Documentation

For detailed product requirements, architectural decisions, MVP boundaries, and acceptance criteria, see:

- [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) — product vision, target workflow, and requirements.
- [`AGENTS.md`](AGENTS.md) — repository instructions for AI coding agents.

These documents should be consulted before implementing significant changes to the application.

## Current masking security boundaries

Credentials matching supported password/API-key assignments, quoted assignments,
JWT/provider-token patterns, and Basic/Bearer Authorization formats are redacted
before generation and mock ticket storage. Detected values repeated within one
processing operation are also redacted. Arbitrary unlabeled values and unknown
credential formats are outside this detection guarantee.

Session masking state stores rule choices and placeholder/category metadata, not
original values. No category is restored at this stage. Selective restoration
remains planned for final review and will require a category-specific policy and
explicit confirmation; personal data stays masked by default. Optional masking
chips remain configurable, while credential rules cannot be disabled.

Editing a preview triggers another backend check. If it changes the text, the
updated preview must be reviewed and submitted again before generation. The
active generator remains a local stub; no live OpenAI integration is enabled.
Session validity and expiry lifecycle improvements remain a separate task.

Synthetic-only backend verification (using existing environment dependencies):

```sh
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v
```
