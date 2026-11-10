# Cookbook: Rewriting Technical Documentation into ASD-STE100

## Objective
Convert complex, ambiguous technical documentation into concise, ASD-STE100 compliant text.

## Prerequisites
- Draft documentation, API guides, or architecture summaries to be simplified.

## Execution Recipe
1. **Sentence Length Audit**:
   Scan draft text. Split any sentence containing more than 20 words into two separate sentences.
2. **Convert Passive Voice to Imperative**:
   - *Draft*: "The configuration file must be modified by the user to contain the port number."
   - *STE100*: "Write the port number in the configuration file."
3. **Replace Ambiguous Verbs**:
   - Replace "make sure that" with "verify that".
   - Replace "carry out the check" with "check the system".
   - Replace "take into consideration" with "consider".
4. **Unpack Noun Clusters**:
   - *Draft*: "User session database connection pool timeout limit" (6 nouns)
   - *STE100*: "Timeout limit for the pool of database connections in a user session"
5. **Ensure Consistent Terminology**:
   Choose one word for a concept and use it exclusively (e.g. do not alternate between "directory", "folder", and "location").
6. **Verification Gate**:
   - Average sentence length $\le$ 18 words.
   - 0 passive voice constructions in procedural instructions.
   - 0 uses of ambiguous modal verbs ("should", "might").
7. **Remediation**:
   If a sentence remains awkward, break it into a bulleted list or a step-by-step table.
