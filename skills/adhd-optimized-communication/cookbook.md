# Cookbook: Formatting Responses for Instant Scannability

## Objective
Transform dense, verbose explanations into structured, scannable, executive-ready responses.

## Prerequisites
- Complex technical finding, bug diagnosis, or strategic proposal to communicate.

## Execution Recipe
1. **Extract the Bottom Line**:
   Formulate the single most important finding or action into one direct sentence:
   - Example: **"Root Cause Identified: PortAudio buffer underrun caused by concurrent mic polling in `audio_io.py:165`."**
2. **Strip Pleasantries and Introductory Context**:
   Delete greeting sentences, rhetorical questions, and restatements of the user's prompt.
3. **Format Key Findings as Bold Bulleted Items**:
   - **The Problem**: Concrete symptom and affected file.
   - **The Fix**: Exact code edit or configuration change.
   - **Verification**: Command executed and test outcome.
4. **Present Next Decisions as Mutually Exclusive Options**:
   Provide 2 to 3 distinct numbered choices with recommended default:
   - `Option 1 (Recommended)`: ...
   - `Option 2`: ...
5. **Verification Gate**:
   - Is the direct answer visible in the first 2 lines without scrolling?
   - Are there any paragraphs longer than 3 sentences?
   - Can the reader scan the bold lead-ins in under 10 seconds?
6. **Remediation**:
   If text feels cluttered, convert prose explanations into a Markdown table or push detailed logs into a persistent artifact.
