please note.

- when agent &amp; Orchestrator Bridge, click probe on Orca ADE Orchestrator - the app crashes. the other probes return green nd work
- in tools &amp; MCP Access when unclicking a tool such as clipboard_processor. the assistant still gives feedback when i copy something, please investigate
- after fresh install using the Brahma_setup.exe the Skills Library section (in the settings) is not showing any skills, including the 3 default ones from before (Reasoning Rigor, Web Research, Word Count), nor any of the ones we made in the agent harness.
- the model setup, for the voice models, and the avatar models is not very streamlined and easy to do for someone who doesn't have developer knowledge, or who has installed only the .exe
- in the Identity &amp; Profile section under Behavior Mode there are 5 presets. we need to investigate them and make them customizable. as i''ve noticed the user can insert custom instructions only at set up time, without the ability to edit them later, nor to know what "professional" or "casual" behavior is set as.
- I've noticed that the GUI for the chat, in the main app, and in the floating workspace has a few issues with truncating the content, 1 from the ai, and that of the audio captured from the human, additionally there might be some sort of delay where i noticed the cli log has captured the message from the the llm, but the gui is slowly streaming word by word. please investigate.
- We need toi look into the Auto0Heal &amp; Continuous Self-Improvement section as well to make sure it is still valid with our recent modifications to the system.
- we need to investigate the memory system as well, to make sure it is a well architected and engineered production ready system, and not a experimental demo. additionally it might be even a great addition if we could add external memory systems of which there are a few available, online systems and offline systems.
- Finally we need to make exhaustive documentation to catch up with what we have built.

---

- i've notice brahma makes proactive comments once a while, help me locate those functions, i would like to be able to control them .
- in the gui, i notice the permission request popup, is fixed small height and doesn't show the overflow (there is around 1 pixel height of the buttons visible. it would be better if we could somehow wrap the whole thing so all the content is visible. A similar thing applies to chat display, sometimes the text content is truncated from the user and from the ai, and some of the event logs in the chat are a bit squeezed. were there isn't enough spacing around the content of to chat bubble.
- attaching a file in the chat crashes the app (gemini live)
- how does the user set up the memory modules?
- 