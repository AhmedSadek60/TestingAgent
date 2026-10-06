from crewai import Agent, Crew, Task

triage = Agent(role="Triage supervisor", goal="Route requests to the right specialist", backstory="...")
billing = Agent(role="Billing specialist", goal="Resolve billing questions", backstory="...")
tech = Agent(role="Technical specialist", goal="Resolve technical issues", backstory="...")
crew = Crew(agents=[triage, billing, tech], tasks=[Task(description="handle request", agent=triage)])
