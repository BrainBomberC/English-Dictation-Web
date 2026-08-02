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

    contentText(content) {
        if (typeof content === "string") return content;
        if (!Array.isArray(content)) return "";
        return content.map(part => {
            if (typeof part === "string") return part;
            if (typeof part?.text === "string") return part.text;
            return "";
        }).filter(Boolean).join("\n");
    }

    cleanFinalAnswer(content) {
        let text = this.contentText(content).trim();
        if (!text) return "";

        // Never expose model reasoning or self-review in the tutor panel.
        text = text.replace(/<think>[\s\S]*?<\/think>/gi, "").trim();
        if (/<think>/i.test(text) && !/<\/think>/i.test(text)) return "";

        const reviewMarker = /(?:^|\n)\s*(?:\d+\.\s*)?(?:Final Review against Constraints|Final Review|Self[- ]?Check|Constraint Check|约束检查|自我检查)\s*:?[ \t]*/i;
        const review = reviewMarker.exec(text);
        if (review) text = text.slice(0, review.index).trim();

        text = text.replace(/^\s*(?:Final Answer|Answer|最终答案)\s*[:：]\s*/i, "").trim();
        return text;
    }

    isIncompleteAnswer(finishReason, text) {
        if (["length", "max_tokens"].includes(String(finishReason || "").toLowerCase())) return true;
        return /(?:^|\n)\s*[-*•]\s*$/.test(text || "");
    }

    appendContinuation(current, continuation) {
        const first = (current || "").replace(/(?:^|\n)\s*[-*•]\s*$/, "").trimEnd();
        const second = (continuation || "").trim();
        if (!first) return second;
        if (!second || first.endsWith(second)) return first;
        return first + "\n" + second;
    }

    async requestCompletion(endpoint, headers, messages) {
        const body = { messages, temperature:0.3, max_tokens:4096, stream:false };
        const model = ($("api-model")?.value||"").trim();
        if (model) body.model = model;
        const response = await fetch(endpoint, { method:"POST", headers, body:JSON.stringify(body) });
        const raw = await response.text();
        let data = {};
        if (raw) {
            try { data = JSON.parse(raw); }
            catch (_) {
                if (!response.ok) throw new Error(`AI 服务请求失败（HTTP ${response.status}）`);
                throw new Error("AI 服务返回了无法解析的数据");
            }
        }
        if (!response.ok) {
            const detail = data?.error?.message || data?.detail || `HTTP ${response.status}`;
            throw new Error("AI 服务请求失败：" + detail);
        }

        const choice = data?.choices?.[0];
        if (!choice?.message) throw new Error("AI 服务没有返回有效答案");
        return {
            content: this.contentText(choice.message.content),
            finishReason: choice.finish_reason || "",
        };
    }

    async askLLM(prompt) {
        const url = ($("api-url")?.value||"").trim().replace(/\/+$/, "");
        if (!url || this._thinking) return;
        this.setThinking(true);
        try {
            this.qaAppend(prompt, "...");
            const sys = ($("sys-prompt")?.value||"").trim();
            const finalOnly = "只输出直接给用户阅读的最终中文答案。不要展示思考过程、分析步骤、自我检查、约束检查或 Final Review。Do not reveal chain-of-thought, hidden reasoning, self-review, or constraint checks.";
            const messages = [
                {role:"system", content:[sys, finalOnly].filter(Boolean).join("\n\n")},
                {role:"user", content:prompt},
            ];
            const headers = { "Content-Type":"application/json" };
            const key = ($("api-key")?.value||"").trim();
            if (key) headers["Authorization"] = "Bearer "+key;

            const endpoint = url + "/chat/completions";
            let activeMessages = messages;
            let result = await this.requestCompletion(endpoint, headers, activeMessages);
            let reply = this.cleanFinalAnswer(result.content);

            // A reasoning model may return only an internal review. Retry once
            // with an explicit final-answer-only user instruction.
            if (!reply) {
                const retryMessages = [
                    messages[0],
                    {role:"user", content:prompt + "\n\n请重新回答，只给出最终中文答案，不要输出任何思考或检查过程。"},
                ];
                activeMessages = retryMessages;
                result = await this.requestCompletion(endpoint, headers, activeMessages);
                reply = this.cleanFinalAnswer(result.content);
            }

            // Continue a genuinely truncated response without displaying a
            // dangling bullet. Limit retries so a broken API cannot loop.
            let continuationCount = 0;
            let conversation = [...activeMessages];
            while (reply && this.isIncompleteAnswer(result.finishReason, reply) && continuationCount < 2) {
                conversation.push({role:"assistant", content:result.content || reply});
                conversation.push({role:"user", content:"请从中断的位置继续完成答案，不要重复已有内容，只输出最终答案的剩余部分。"});
                result = await this.requestCompletion(endpoint, headers, conversation);
                const continuation = this.cleanFinalAnswer(result.content);
                if (!continuation) break;
                reply = this.appendContinuation(reply, continuation);
                continuationCount++;
            }

            if (!reply) throw new Error("模型只返回了思考或检查过程，没有返回最终答案");
            if (this.isIncompleteAnswer(result.finishReason, reply)) {
                reply += "\n\n[回答仍被模型截断，请重试或更换模型]";
            }
            this.qaAppend(prompt, reply);
        } catch(e) { this.qaAppend("Error", e.message); }
        finally { this.setThinking(false); }
    }

    fmt(s) { const m=Math.floor(s/60); const sec=Math.floor(s%60); return `${m}:${String(sec).padStart(2,'0')}`; }
    esc(s) { const d=document.createElement("div"); d.textContent=s; return d.innerHTML; }
}

let app;
document.addEventListener("DOMContentLoaded", () => { try { app = new App(); } catch(e) { console.error(e); } });
