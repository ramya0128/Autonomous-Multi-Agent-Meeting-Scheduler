"""Script to generate a comprehensive, publication-quality PDF guide
explaining the Agent Registry, A2A communication, and discovery architecture.
"""
import os
import sys
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        
        # Header (pages > 1)
        if self._pageNumber > 1:
            self.drawString(40, 755, "A2A PROTOCOL & MULTI-AGENT ARCHITECTURE GUIDE")
            self.drawRightString(572, 755, "GOOGLE A2A PROTOCOL DEEP DIVE")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.75)
            self.line(40, 747, 572, 747)
            
        # Footer
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(572, 28, page_text)
        self.drawString(40, 28, "A2A Meetings System — Agent Registry & Inter-Agent Communication Specification")
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.75)
        self.line(40, 38, 572, 38)
        self.restoreState()


def build_pdf(filename: str):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=40,
        rightMargin=40,
        topMargin=54,
        bottomMargin=50,
    )
    
    styles = getSampleStyleSheet()
    
    # Custom palette
    PRIMARY = colors.HexColor("#1E293B")     # Dark Slate
    ACCENT = colors.HexColor("#2563EB")      # Vibrant Blue
    ACCENT_PURPLE = colors.HexColor("#7C3AED")# Deep Violet
    MUTED = colors.HexColor("#64748B")       # Cool Grey
    LIGHT_BG = colors.HexColor("#F8FAFC")    # Slate 50
    CODE_BG = colors.HexColor("#0F172A")     # Night Navy
    BORDER_COL = colors.HexColor("#E2E8F0")  # Slate 200
    GREEN = colors.HexColor("#10B981")       # Emerald

    # Styles
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=24,
        leading=28,
        textColor=PRIMARY,
        spaceAfter=6,
    )
    
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=12,
        leading=16,
        textColor=MUTED,
        spaceAfter=15,
    )
    
    h1_style = ParagraphStyle(
        "SectionH1",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor=PRIMARY,
        spaceBefore=14,
        spaceAfter=6,
        keepWithNext=True,
    )

    h2_style = ParagraphStyle(
        "SectionH2",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=15,
        textColor=ACCENT,
        spaceBefore=10,
        spaceAfter=4,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        "DocBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13.5,
        textColor=colors.HexColor("#334155"),
        spaceAfter=6,
    )

    bullet_style = ParagraphStyle(
        "DocBullet",
        parent=body_style,
        leftIndent=14,
        firstLineIndent=-10,
        spaceAfter=3,
    )

    code_style = ParagraphStyle(
        "CodeBlock",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=7.8,
        leading=10.5,
        textColor=colors.HexColor("#E2E8F0"),
    )
    
    badge_style = ParagraphStyle(
        "BadgeText",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
    )

    callout_style = ParagraphStyle(
        "CalloutText",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.8,
        leading=12.5,
        textColor=colors.HexColor("#1E3A8A"),
    )

    def code_box(code_text: str):
        # Format text for reportlab Paragraph with basic XML escape
        escaped = code_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines = escaped.strip().split("\n")
        content = "<br/>".join(lines).replace(" ", "&nbsp;")
        p = Paragraph(content, code_style)
        t = Table([[p]], colWidths=[532])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), CODE_BG),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LEFTPADDING', (0,0), (-1,-1), 10),
            ('RIGHTPADDING', (0,0), (-1,-1), 10),
            ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#1E293B")),
            ('CORNERPAD', (0,0), (-1,-1), 4),
        ]))
        return t

    def callout_box(title: str, text: str, border_color=ACCENT, bg_color=colors.HexColor("#EFF6FF")):
        content = [
            Paragraph(f"<b>{title}</b>", ParagraphStyle("CTitle", parent=callout_style, fontName="Helvetica-Bold", textColor=border_color, spaceAfter=3)),
            Paragraph(text, callout_style)
        ]
        t = Table([[content]], colWidths=[532])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), bg_color),
            ('TOPPADDING', (0,0), (-1,-1), 7),
            ('BOTTOMPADDING', (0,0), (-1,-1), 7),
            ('LEFTPADDING', (0,0), (-1,-1), 10),
            ('RIGHTPADDING', (0,0), (-1,-1), 10),
            ('BOX', (0,0), (-1,-1), 1, border_color),
        ]))
        return t

    story = []

    # =========================================================================
    # HEADER / TITLE BLOCK
    # =========================================================================
    header_table = Table([
        [
            Paragraph("A2A MULTI-AGENT ARCHITECTURE & REGISTRY SPECIFICATION", ParagraphStyle("HBadge", parent=badge_style, textColor=ACCENT, fontSize=9)),
            Paragraph("OCTOBER 2026 • SYSTEM DESIGN", ParagraphStyle("HDate", parent=styles["Normal"], fontName="Helvetica", fontSize=8.5, textColor=MUTED, alignment=2))
        ]
    ], colWidths=[350, 182])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=1.5, color=ACCENT, spaceBefore=2, spaceAfter=10))

    story.append(Paragraph("How the Agent Registry & A2A Communication Work", title_style))
    story.append(Paragraph("Deep-dive architectural report detailing agent self-registration, identity resolution, the Google A2A protocol handshake, and production patterns.", subtitle_style))

    # =========================================================================
    # 1. ARCHITECTURE OVERVIEW
    # =========================================================================
    story.append(Paragraph("1. High-Level Multi-Agent Architecture", h1_style))
    story.append(Paragraph(
        "In this autonomous scheduling platform, the system is decomposed into a <b>Company Coordinator Agent</b> "
        "(orchestrating overall scheduling intents) and multiple distributed <b>Employee Domain Agents</b> "
        "(Priya, Arjun, Meera), each safeguarding an individual's calendar, privacy boundaries, and rules.",
        body_style
    ))

    # Visual Architecture Table
    arch_flow_data = [
        [
            Paragraph("<b>USER / ORGANIZER</b><br/><font color='#64748B'>Web UI / CLI Chat</font>", ParagraphStyle("AC1", parent=body_style, alignment=1, fontSize=8)),
            Paragraph("──(NLP Intent)──&gt;", ParagraphStyle("AArr", parent=body_style, alignment=1, fontSize=8, textColor=MUTED)),
            Paragraph("<b>COMPANY AGENT</b><br/><font color='#64748B'>Orchestrator (Port 8000)</font>", ParagraphStyle("AC2", parent=body_style, alignment=1, fontSize=8)),
            Paragraph("──(A2A Protocol)──&gt;", ParagraphStyle("AArr2", parent=body_style, alignment=1, fontSize=8, textColor=MUTED)),
            Paragraph("<b>EMPLOYEE AGENTS</b><br/><font color='#64748B'>Priya (9101) | Arjun (9102)</font>", ParagraphStyle("AC3", parent=body_style, alignment=1, fontSize=8))
        ],
        [
            Paragraph("", body_style),
            Paragraph("", body_style),
            Paragraph("<b>AGENT REGISTRY</b><br/><font color='#64748B'><code>agent_registry.json</code></font>", ParagraphStyle("AC4", parent=body_style, alignment=1, fontSize=8)),
            Paragraph("&lt;--(Discovery)--", ParagraphStyle("AArr3", parent=body_style, alignment=1, fontSize=8, textColor=MUTED)),
            Paragraph("<b>REGISTRATION</b><br/><font color='#64748B'>Self-registers on boot</font>", ParagraphStyle("AC5", parent=body_style, alignment=1, fontSize=8))
        ]
    ]
    arch_table = Table(arch_flow_data, colWidths=[110, 80, 130, 80, 132])
    arch_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,0), LIGHT_BG),
        ('BACKGROUND', (2,0), (2,0), colors.HexColor("#EFF6FF")),
        ('BACKGROUND', (4,0), (4,0), LIGHT_BG),
        ('BACKGROUND', (2,1), (2,1), colors.HexColor("#F3E8FF")),
        ('BACKGROUND', (4,1), (4,1), LIGHT_BG),
        ('BOX', (0,0), (0,0), 1, BORDER_COL),
        ('BOX', (2,0), (2,0), 1, ACCENT),
        ('BOX', (4,0), (4,0), 1, BORDER_COL),
        ('BOX', (2,1), (2,1), 1, ACCENT_PURPLE),
        ('BOX', (4,1), (4,1), 1, BORDER_COL),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(arch_table)
    story.append(Spacer(1, 10))

    # =========================================================================
    # 2. THE AGENT REGISTRY
    # =========================================================================
    story.append(Paragraph("2. The Agent Registry: How it Works", h1_style))
    story.append(Paragraph(
        "The registry acts as the <b>phonebook and capability broker</b>. Rather than hardcoding URLs or ports into "
        "the company coordinator, agents dynamically publish their endpoints, skills, and availability.",
        body_style
    ))
    story.append(Paragraph("<b>Key Registry Components in Code:</b>", body_style))
    story.append(Paragraph("• <b>File Store:</b> <code>data/agent_registry.json</code> persists registered agents across restarts.", bullet_style))
    story.append(Paragraph("• <b>Thread Safety:</b> Atomic replacement via <code>NamedTemporaryFile</code>, <code>os.fsync</code>, and <code>threading.RLock</code> prevents race conditions and corrupted reads.", bullet_style))
    story.append(Paragraph("• <b>Dynamic Proxy:</b> <code>RegistryEmployeesMapping</code> implements Python's <code>MutableMapping</code> interface so legacy dictionary lookups read from the live registry seamlessly.", bullet_style))
    story.append(Spacer(1, 4))

    story.append(Paragraph("Step 1: Agent Self-Registration on Startup", h2_style))
    story.append(Paragraph(
        "When an employee agent starts (e.g. <code>python employee_agent.py priya 9101</code>), it defines its "
        "A2A Agent Card and invokes <code>agent_registry.register_agent()</code>:",
        body_style
    ))
    story.append(code_box(
"""# agents/employee_agent.py (Lines 248-262)
card = make_card(f"{name.title()}'s Calendar Agent", ..., port, skill)

# Dynamic registration with the central agent registry
agent_registry.register_agent(
    agent_id=name,                      # "priya"
    url=f"http://127.0.0.1:{port}",      # "http://127.0.0.1:9101"
    name=display_name,                  # "Priya Sharma"
    role="employee",
    skills=["calendar", "meetings", "book_meeting"],
    status="online",
)
serve(card, EmployeeExecutor(name), port)"""
    ))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Resulting Registry Record (<code>data/agent_registry.json</code>):", h2_style))
    story.append(code_box(
"""{
  "priya": {
    "id": "priya",
    "name": "Priya Sharma",
    "url": "http://127.0.0.1:9101",
    "role": "employee",
    "skills": ["calendar", "meetings", "book_meeting"],
    "status": "online",
    "registered_at": "2026-10-23T00:00:00+05:30",
    "last_seen": "2026-10-24T07:47:18+05:30"
  }
}"""
    ))
    story.append(Spacer(1, 8))

    # =========================================================================
    # 3. HOW THE COMPANY AGENT DISCOVERS AGENTS
    # =========================================================================
    story.append(PageBreak())
    story.append(Paragraph("3. How Company Agent Discovers Agents (Semantic Search vs Deterministic Lookup)", h1_style))
    
    story.append(callout_box(
        "Important Distinction: Is Semantic Search Used Here?",
        "<b>No.</b> In calendar scheduling, attendee identity resolution is <b>deterministic</b>. "
        "When a user asks to meet with 'Priya', resolving via vector similarity risks fuzzy mismatches (e.g., returning 'Priyanka'). "
        "Exact string normalization is used for identity, while semantic search is reserved for open-ended skill discovery.",
        border_color=ACCENT_PURPLE,
        bg_color=colors.HexColor("#F5F3FF")
    ))
    story.append(Spacer(1, 8))

    story.append(Paragraph("The Dynamic Dict Proxy (<code>RegistryEmployeesMapping</code>)", h2_style))
    story.append(Paragraph(
        "In <code>agents/company_tools.py</code>, line 32 sets <code>EMPLOYEES = agent_registry.RegistryEmployeesMapping()</code>. "
        "Any lookup like <code>EMPLOYEES['priya']</code> executes live queries against the registry:",
        body_style
    ))
    story.append(code_box(
"""# agents/agent_registry.py (Lines 303-349)
class RegistryEmployeesMapping(MutableMapping):
    def __getitem__(self, key):
        urls = get_employee_urls()             # Reads data/agent_registry.json
        norm_key = str(key).strip().lower()
        if norm_key in urls:
            return urls[norm_key]             # Returns "http://127.0.0.1:9101"
        raise KeyError(key)

    def __contains__(self, key):
        return str(key).strip().lower() in get_employee_urls()"""
    ))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Validation and Connection in <code>company_tools.py</code>", h2_style))
    story.append(Paragraph(
        "When the LLM decides to invite attendees, it executes <code>create_meeting</code> or <code>send_invite</code>. "
        "The tool validates the attendees against the live registry:",
        body_style
    ))
    story.append(code_box(
"""# agents/company_tools.py (Lines 495-504)
for invitee in invitees:
    if not isinstance(invitee, str) or invitee.strip().lower() not in EMPLOYEES:
        return f"ERROR: unknown employee '{invitee}'. Known: {', '.join(EMPLOYEES)}"
    
    employee = invitee.strip().lower()
    normalized_invitees.append(employee)

# Direct connection resolution (Line 965):
client = await connect(EMPLOYEES[employee])  # connect("http://127.0.0.1:9101")"""
    ))
    story.append(Spacer(1, 8))

    # =========================================================================
    # 4. A2A PROTOCOL HANDSHAKE & COMMUNICATION
    # =========================================================================
    story.append(Paragraph("4. How Agents Communicate Over A2A (Protocol Handshake)", h1_style))
    story.append(Paragraph(
        "Communication between agents uses Google's <b>A2A (Agent-to-Agent) Standard Protocol</b>. "
        "It operates in two distinct phases: <i>Card Resolution</i> and <i>Stateful Streaming Session</i>.",
        body_style
    ))
    
    # Handshake Steps Table
    handshake_data = [
        [
            Paragraph("<b>Phase</b>", ParagraphStyle("TH1", parent=body_style, fontName="Helvetica-Bold", textColor=PRIMARY)),
            Paragraph("<b>Action & Endpoint</b>", ParagraphStyle("TH2", parent=body_style, fontName="Helvetica-Bold", textColor=PRIMARY)),
            Paragraph("<b>Payload / Outcome</b>", ParagraphStyle("TH3", parent=body_style, fontName="Helvetica-Bold", textColor=PRIMARY))
        ],
        [
            Paragraph("<b>1. Card Resolution</b>", body_style),
            Paragraph("<code>GET /.well-known/agent-card.json</code><br/>HTTP GET to employee agent URL", body_style),
            Paragraph("Returns Agent Card JSON: protocol version, capabilities, skills, and authentication schema.", body_style)
        ],
        [
            Paragraph("<b>2. Client Init</b>", body_style),
            Paragraph("<code>create_client(card, config)</code><br/>ClientConfig(streaming=True)", body_style),
            Paragraph("Initializes HTTPX streaming client with auth token headers.", body_style)
        ],
        [
            Paragraph("<b>3. Message Dispatch</b>", body_style),
            Paragraph("<code>client.send_message()</code><br/>A2A SendMessageRequest", body_style),
            Paragraph("Payload contains meeting JSON, UUID message ID, optional <code>task_id</code> and <code>context_id</code>.", body_style)
        ],
        [
            Paragraph("<b>4. Event Stream</b>", body_style),
            Paragraph("Async Server-Sent Events (SSE)<br/><code>task</code>, <code>status_update</code>, <code>artifact_update</code>", body_style),
            Paragraph("Streams execution progress and emits task status (<code>COMPLETED</code>, <code>INPUT_REQUIRED</code>, <code>REJECTED</code>).", body_style)
        ]
    ]
    ht_table = Table(handshake_data, colWidths=[110, 180, 242])
    ht_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), LIGHT_BG),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_COL),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(ht_table)
    story.append(Spacer(1, 8))

    story.append(Paragraph("Code Behind A2A Handshake (<code>agents/a2a_client.py</code>)", h2_style))
    story.append(code_box(
"""# agents/a2a_client.py (Lines 22-57)
async def get_agent_card(url: str):
    async with httpx.AsyncClient(timeout=5, headers=headers) as http:
        # Step 1: Resolves agent card from standard well-known endpoint
        return await A2ACardResolver(http, url).get_agent_card()

async def connect(url: str):
    card = await get_agent_card(url)
    http = httpx.AsyncClient(timeout=30, headers=headers)
    # Step 2: Creates A2A client from discovered card
    return await create_client(card, client_config=ClientConfig(streaming=True, httpx_client=http))

async def call(client, text, task_id=None, context_id=None):
    # Step 3: Transmits message and listens to event stream
    async for ev in client.send_message(SendMessageRequest(
            message=user_message(text, task_id, context_id))):
        if ev.HasField("task"):
            state = TaskState.Name(ev.task.status.state)
        elif ev.HasField("artifact_update"):
            artifact = get_artifact_text(ev.artifact_update.artifact)
    return state, artifact, task_id, context_id"""
    ))

    # =========================================================================
    # 5. STATEFUL NEGOTIATION LIFECYCLE
    # =========================================================================
    story.append(PageBreak())
    story.append(Paragraph("5. Stateful Negotiation & Conflict Resolution Flow", h1_style))
    story.append(Paragraph(
        "A central innovation of the A2A design is that negotiations are not simple fire-and-forget RPCs. "
        "They maintain <b>durable, resumable task contexts</b>.",
        body_style
    ))

    story.append(Paragraph("The Four Core Task States", h2_style))
    states_data = [
        [
            Paragraph("<code>TASK_STATE_WORKING</code>", body_style),
            Paragraph("Agent is analyzing rules, checking calendar free/busy slots, or querying external APIs.", body_style)
        ],
        [
            Paragraph("<code>TASK_STATE_COMPLETED</code>", body_style),
            Paragraph("Meeting accepted and booked on calendar. Event JSON returned as artifact payload.", body_style)
        ],
        [
            Paragraph("<code>TASK_STATE_INPUT_REQUIRED</code>", body_style),
            Paragraph("A clash or policy question was found. The agent pauses execution and yields control to the human employee.", body_style)
        ],
        [
            Paragraph("<code>TASK_STATE_REJECTED / FAILED</code>", body_style),
            Paragraph("Meeting was declined due to strict personal rule, existing immovable event, or user refusal.", body_style)
        ]
    ]
    st_table = Table(states_data, colWidths=[170, 362])
    st_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), LIGHT_BG),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_COL),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(st_table)
    story.append(Spacer(1, 8))

    story.append(Paragraph("What happens when Priya has a conflict? (Step-by-Step)", h2_style))
    story.append(Paragraph(
        "1. Company Agent sends invite for 'Product Review' at 10:30 AM to Priya's agent over A2A.<br/>"
        "2. Priya's Agent inspects her calendar, finds an overlapping 'Client Call'.<br/>"
        "3. Priya's Agent returns <code>TASK_STATE_INPUT_REQUIRED</code>: <i>'Client Call overlaps. Reschedule or decline?'</i><br/>"
        "4. Company Tools record this session in <code>data/tasks.json</code> with a <code>task_id</code> and <code>context_id</code>.<br/>"
        "5. Priya logs into her Employee Dashboard, sees the decision card, and selects <i>'Reschedule client call'</i>.<br/>"
        "6. Dashboard calls <code>reply_to_employee(meeting_id, employee, reply)</code> which sends the answer back to "
        "Priya's agent using the exact same <code>task_id</code> and <code>context_id</code>, resuming the original task!",
        body_style
    ))
    story.append(Spacer(1, 8))

    # =========================================================================
    # 6. LOCAL PROTOTYPE VS PRODUCTION ENTERPRISE
    # =========================================================================
    story.append(Paragraph("6. Local Prototype vs. Production Enterprise Architecture", h1_style))
    story.append(Paragraph(
        "In production, while the protocol concepts (Agent Cards, Task States, Tool Loops) stay identical, "
        "the infrastructure layers are hardened for massive concurrency, high availability, and zero-trust security.",
        body_style
    ))

    comp_data = [
        [
            Paragraph("<b>Architecture Layer</b>", ParagraphStyle("CH1", parent=body_style, fontName="Helvetica-Bold")),
            Paragraph("<b>Current Prototype</b>", ParagraphStyle("CH2", parent=body_style, fontName="Helvetica-Bold")),
            Paragraph("<b>Enterprise Production Grade</b>", ParagraphStyle("CH3", parent=body_style, fontName="Helvetica-Bold"))
        ],
        [
            Paragraph("<b>Registry & Discovery</b>", body_style),
            Paragraph("Local JSON file (<code>agent_registry.json</code>) with filesystem atomic locks.", body_style),
            Paragraph("Distributed consensus store (<b>etcd</b>, <b>Consul</b>, <b>Redis Cluster</b>) or Kubernetes CoreDNS service discovery.", body_style)
        ],
        [
            Paragraph("<b>Agent Addressing</b>", body_style),
            Paragraph("Localhost static ports (<code>http://127.0.0.1:9101</code>).", body_style),
            Paragraph("Internal Service Mesh URIs (<b>Envoy / Istio</b>) with load balancing and circuit breaking.", body_style)
        ],
        [
            Paragraph("<b>Security & Auth</b>", body_style),
            Paragraph("Shared static Bearer token.", body_style),
            Paragraph("<b>mTLS</b> with SPIFFE/SPIRE machine IDs + <b>OAuth2 Token Exchange (RFC 8693)</b> for user-delegated authority.", body_style)
        ],
        [
            Paragraph("<b>Skill Discovery</b>", body_style),
            Paragraph("Static skill tag list strings.", body_style),
            Paragraph("<b>pgvector / Pinecone</b> vector embeddings of Agent Cards for semantic semantic-skill routing.", body_style)
        ],
        [
            Paragraph("<b>Durable Execution</b>", body_style),
            Paragraph("In-memory ThreadPool + JSON task file.", body_style),
            Paragraph("Durable workflow orchestration (<b>Temporal.io</b>, AWS Step Functions, Kafka event queues) surviving restarts.", body_style)
        ]
    ]
    comp_table = Table(comp_data, colWidths=[120, 190, 222])
    comp_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), LIGHT_BG),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_COL),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(comp_table)
    story.append(Spacer(1, 12))

    # Concluding Callout
    story.append(callout_box(
        "Conclusion: The Power of A2A",
        "By enforcing strict A2A boundaries, agents maintain domain autonomy. The Company Agent never reads "
        "or manipulates Priya's raw calendar data directly; it sends an invitation proposal and Priya's personal agent "
        "applies her private constraints, rules, and human confirmations before committing.",
        border_color=GREEN,
        bg_color=colors.HexColor("#F0FDF4")
    ))

    # Build Document
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Successfully generated PDF: {filename}")


if __name__ == "__main__":
    output_pdf = str(Path(__file__).resolve().parent / "A2A_Agent_Registry_and_Communication_Guide.pdf")
    build_pdf(output_pdf)
