const $ = id => document.getElementById(id);

class App {
    constructor() {
        this.videos = []; this.segments = []; this.currentIdx = -1;
        this.repeatCount = 5; this.currentRepeat = 0; this.autoNext = false; this._thinking = false;
        this.loadVideos(); this.bindEvents();
    }

    async loadVideos() {
        try { const r = await fetch("/api/videos"); this.videos = await r.json(); this.renderTabs(); }
        catch(e) { const list = $("sidebar-list"); if (list) list.innerHTML = 'Load failed'; }
    }

    renderTabs() {
        const list = $("sidebar-list"); if (!list) return;
        list.innerHTML = this.videos.map(v => `<div class="svid" data-id="${v.id}" onclick="app.selectVideo('${v.id}')">${this.esc(v.name)}</div>`).join("");
    }

    async selectVideo(id) {
        document.querySelectorAll(".svid").forEach(e => e.classList.remove("active"));
        document.querySelector(`.svid[data-id="${id}"]`)?.classList.add("active");
        $("empty").classList.add("hidden"); $("player").classList.remove("hidden");
        $("video").src = `/api/videos/${id}/video`;
        try { this.segments = await fetch(`/api/videos/${id}/segments`).then(r => r.json()); this.currentIdx = 0; this.selectSegment(0); this.renderSegList(); }
        catch(e) { console.error(e); }
    }

    renderSegList() {
        $("seglist").innerHTML = this.segments.map((s, i) => `<div class="segrow ${i===this.currentIdx?'on':''}" onclick="app.selectSegment(${i})"><span class="n">${i+1}</span><span class="t">${this.fmt(s.start)}-${this.fmt(s.end)}</span><span class="x">${this.esc(s.text)}</span></div>`).join("");
    }

    selectSegment(i) {
        if (i<0 || i>=this.segments.length) return;
        this.currentIdx = i; this.currentRepeat = 0;
        const s = this.segments[i];
        $("text").textContent = s.text; $("stamp").textContent = this.fmt(s.start) + " - " + this.fmt(s.end);
        $("video").currentTime = s.start; $("video").play();
        this.updateInfo(); this.renderSegList();
    }

    playSegment() {
        const s = this.segments[this.currentIdx]; if (!s) return;
        $("video").currentTime = s.start; $("video").play();
        this.currentRepeat = 0; this.updateInfo();
    }

    bindEvents() {
        const b = (id, fn) => { const e = $(id); if (e) e.onclick = fn; };
        b("prev", () => this.selectSegment(this.currentIdx-1));
        b("next", () => this.selectSegment(this.currentIdx+1));
        b("play", () => this.playSegment());
        b("repeat", () => { this.currentRepeat=0; this.playSegment(); });
        b("jumpbtn", () => { const n=parseInt($("jump").value)-1; if(n>=0 && n<this.segments.length) this.selectSegment(n); });
        const rc = $("rc"); if(rc) rc.onchange = () => this.repeatCount = parseInt(rc.value);
        const sp = $("sp"); if(sp) sp.onchange = () => $("video").playbackRate = parseFloat(sp.value);
        const au = $("auto"); if(au) au.onchange = () => this.autoNext = au.checked;
        $("video").addEventListener("timeupdate", () => {
            const s = this.segments[this.currentIdx]; if (!s || $("video").paused) return;
            if ($("video").currentTime >= s.end) { $("video").pause(); this.currentRepeat++;
                if (this.currentRepeat < this.repeatCount) setTimeout(() => { $("video").currentTime = s.start; $("video").play(); }, 800);
                else if (this.autoNext) setTimeout(() => this.selectSegment(this.currentIdx+1), 500);
                this.updateInfo(); }
        });
        document.addEventListener("keydown", e => {
            if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
            if (e.code === "ArrowLeft") this.selectSegment(this.currentIdx-1);
            if (e.code === "ArrowRight") this.selectSegment(this.currentIdx+1);
            if (e.code === "Space") { e.preventDefault(); this.playSegment(); }
            if (e.code === "KeyR") { this.currentRepeat=0; this.playSegment(); }
        });
        b("qa-ask", () => { const q = $("qa-input")?.value?.trim(); if (q) { this.askLLM(q); $("qa-input").value = ""; } });
        b("qa-explain", () => { const s = this.segments[this.currentIdx]; if (s) this.askLLM("请用中文解释这个英文句子的意思：\"" + s.text + "\""); });
        b("qa-words", () => { const s = this.segments[this.currentIdx]; if (s) this.askLLM("列出这个英文句子中的关键词和短语，给出中文释义：\"" + s.text + "\""); });
        b("qa-grammar", () => { const s = this.segments[this.currentIdx]; if (s) this.askLLM("分析这个英文句子的语法结构，用中文说明：\"" + s.text + "\""); });
    }

    updateInfo() { $("counter").textContent = `${this.currentIdx+1} / ${this.segments.length}`; $("rcounter").textContent = `Repeat ${this.currentRepeat}/${this.repeatCount}`; }

    

    qaAppend(q, a) {
        const area = $("qa-area"); if (!area) return;
        if (area.innerHTML.includes('color:var(--sub)')) area.innerHTML = "";
        if (a === "...") { area.innerHTML += '<div class="q">'+this.esc(q)+'</div><div class="a thinking" id="think-'+Date.now()+'"><span></span><span></span><span></span></div>'; }
        else { area.innerHTML = area.innerHTML.replace(/<div class="a thinking" id="think-\d+">.*?<\/div>/g,''); area.innerHTML += '<div class="a">'+this.esc(a).replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/\n\n/g,'<br><br>').replace(/\n/g,'<br>')+'</div>'; }
        area.scrollTop = area.scrollHeight;
    }

    setThinking(v) {
        this._thinking = v;
        ["qa-explain","qa-words","qa-grammar","qa-ask"].forEach(id => { const b = $(id); if (b) b.disabled = v; });
    }

    async askLLM(prompt) {
        const url = ($("api-url")?.value||"").trim();
        if (!url || this._thinking) return;
        this.setThinking(true);
        try {
            this.qaAppend(prompt, "...");
            const sys = ($("sys-prompt")?.value||"").trim();
            const body = { messages:[{role:"system",content:sys},{role:"user",content:prompt}], temperature:0.7, max_tokens:800 };
            const headers = { "Content-Type":"application/json" };
            const key = ($("api-key")?.value||"").trim();
            if (key) headers["Authorization"] = "Bearer "+key;
            const r = await fetch(url+"/chat/completions", { method:"POST", headers, body:JSON.stringify(body) });
            const d = await r.json();
            const msg = d.choices?.[0]?.message||{};
            let reply = msg.content || "";
            // If content is empty (thinking mode consumed all tokens), try to extract final answer
            if (!reply && msg.reasoning_content) {
                // Take last meaningful paragraph as the answer
                const lines = msg.reasoning_content.split("\n").filter(l => l.trim());
                reply = lines.slice(-3).join("\n");
            }
            if (!reply) reply = JSON.stringify(d).substring(0, 200);
            this.qaAppend(prompt, reply);
        } catch(e) { this.qaAppend("Error", e.message); }
        this.setThinking(false);
    }

    fmt(s) { const m=Math.floor(s/60); const sec=Math.floor(s%60); return `${m}:${String(sec).padStart(2,'0')}`; }
    esc(s) { const d=document.createElement("div"); d.textContent=s; return d.innerHTML; }
}

let app;
document.addEventListener("DOMContentLoaded", () => { try { app = new App(); } catch(e) { console.error(e); } });