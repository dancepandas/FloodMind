(function () {
  'use strict';
  // 本地无感鉴权模式下没有可用的登录表单：服务重启导致旧页面 websocket 失效时
  // 前端会跳到 /login 并卡死，这里自动送回主页重建会话（sessionStorage 防循环）。
  if (window.location.pathname === '/login' && !sessionStorage.getItem('fm-login-redirect')) {
    sessionStorage.setItem('fm-login-redirect', '1');
    window.location.replace('/');
    return;
  }

  var BRAND_NAME = 'FloodMind';
  var BRAND_TAGLINE = '您的智能水文助手';

  // welcome 页 logo 后面追加副标题；侧栏 logo 旁追加品牌名。
  // 复用 MutationObserver 应对 SPA 路由切换（welcome/sidebar 在 mount 时才进 DOM）。
  function enhanceWelcome(root) {
    var scope = root || document;
    // welcome 容器：img.logo 且其尺寸类带 w-[200px]
    var candidates = scope.querySelectorAll('img.logo.w-\\[200px\\]');
    for (var i = 0; i < candidates.length; i++) {
      var img = candidates[i];
      var parent = img.parentElement;
      if (!parent || parent.querySelector(':scope > .fm-tagline')) continue;
      var tag = document.createElement('div');
      tag.className = 'fm-tagline';
      tag.textContent = BRAND_NAME + ' · ' + BRAND_TAGLINE;
      parent.appendChild(tag);
    }
  }

function enhanceHeader(root) {
    var scope = root || document;
    // 聊天主区顶部 header (id=header) 第一个 .flex.items-center 是空的 brand
    // slot——把品牌 logo + 名称注入到这里，与下方折叠栏左缘齐平。
    var headers = scope.querySelectorAll('#header > .flex.items-center');
    for (var i = 0; i < headers.length; i++) {
      var slot = headers[i];
      if (slot.querySelector('.fm-header-brand') || slot.children.length > 0) continue;
      var brand = document.createElement('div');
      brand.className = 'fm-header-brand';
      brand.innerHTML =
        '<img class="fm-header-logo" src="/public/logo_dark.svg" alt="FloodMind">' +
        '<span class="fm-header-name">FloodMind</span>';
      slot.appendChild(brand);
    }
  }

  function enhanceSidebar(root) {
    var scope = root || document;
    // 历史会话侧栏顶部加品牌行（侧栏标识）
    var sidebars = scope.querySelectorAll('[data-sidebar="sidebar"]');
    for (var i = 0; i < sidebars.length; i++) {
      var sb = sidebars[i];
      if (sb.querySelector(':scope > .fm-brand-bar')) continue;
      var bar = document.createElement('div');
      bar.className = 'fm-brand-bar';
      bar.innerHTML =
        '<img class="fm-brand-logo" src="/public/logo_dark.svg" alt="FloodMind">' +
        '<span class="fm-brand-name">FloodMind</span>';
      sb.insertBefore(bar, sb.firstChild);
    }
    // 兼容老目标（auth layout 的 w-[150px] logo）— 已被 CSS 缩到 28px
    var imgs = scope.querySelectorAll('img.logo.w-\\[150px\\]');
    for (var j = 0; j < imgs.length; j++) {
      var img = imgs[j];
      var parent = img.parentElement;
      if (!parent || parent.querySelector(':scope > .fm-brand')) continue;
      var brand = document.createElement('span');
      brand.className = 'fm-brand';
      brand.appendChild(img.cloneNode(true));
      brand.appendChild(document.createTextNode(BRAND_NAME));
      parent.appendChild(brand);
      img.style.display = 'none';
    }
  }

  function enhance() {
    enhanceWelcome();
    enhanceHeader();
    enhanceSidebar();
    // 隐藏右上角 "说明" 按钮（README 弹窗），与品牌定位无关且本服务无 README
    var rb = document.querySelector('#readme-button');
    if (rb && rb.style.display !== 'none') {
      rb.style.display = 'none';
      rb.style.visibility = 'hidden';
    }
    // 活动摘要（折叠栏下方的最近 2 条过程活动 15 字摘要）：
    // 后端 cl.Message content 嵌入 <span data-fm-act="N"> 标记，Chainlit
    // markdown 渲染器不解析 raw HTML（react-markdown 默认）——标记作为
    // 纯文本保留在 textContent。前端用 textContent 包含 'data-fm-act="' 识别
    // activity message，并把含标记的 text node 切分：把 <span...> 之前的内容
    // 颜色设为透明（视觉上不可见），保留正常摘要内容。
    var activityAll = Array.from(document.querySelectorAll('.ai-message')).filter(function (m) {
      return (m.textContent || '').indexOf('data-fm-act="') !== -1;
    });
    activityAll.forEach(function (m, i) {
      if (m.__fmActProcessed) return;
      m.__fmActProcessed = true;
      // 找含标记的 text node，split + 把含 <span data-fm-act="..."></span>
      // 整段（含闭合标签）包成透明 span（React 不会触碰 message.content DOM）
      var walker = document.createTreeWalker(m, NodeFilter.SHOW_TEXT, null);
      while (walker.nextNode()) {
        var n = walker.currentNode;
        var v = n.nodeValue || '';
        var idx = v.indexOf('<span data-fm-act="');
        if (idx === -1) continue;
        // 找对应的 </span> 闭合标签
        var endIdx = v.indexOf('</span>', idx);
        if (endIdx === -1) continue;
        var hiddenEnd = endIdx + '</span>'.length;
        // 把 [idx, hiddenEnd) 那段包成透明 span
        var before = v.substring(0, idx);
        var hidden = v.substring(idx, hiddenEnd);
        var after = v.substring(hiddenEnd);
        var parent = n.parentNode;
        if (!parent) continue;
        var beforeNode = document.createTextNode(before);
        var hiddenSpan = document.createElement('span');
        hiddenSpan.style.color = 'transparent';
        hiddenSpan.style.fontSize = '0';
        hiddenSpan.textContent = hidden;
        var afterNode = document.createTextNode(after);
        parent.insertBefore(afterNode, n.nextSibling);
        parent.insertBefore(hiddenSpan, afterNode);
        parent.insertBefore(beforeNode, hiddenSpan);
        parent.removeChild(n);
        break;
      }
      m.classList.add('fm-activity');
      // 倒数第 2（含）之前的为 stale
      if (i < activityAll.length - 2) m.classList.add('fm-activity-stale');
    });

    // 任务完成（Chainlit 发出 task_end socket 事件）后，永久隐藏所有活动
    // 摘要——用户只看到折叠头 + 最终回答两件事。靠 socket 42["task_end",{}]
    // 触发，依赖 socket.io 连接。
    function hideAllActivity() {
      if (window.__fmFinalSeen) return;
      window.__fmFinalSeen = true;
      document.querySelectorAll('.fm-activity').forEach(function (a) {
        a.style.display = 'none';
      });
    }
    if (!window.__fmTaskEndBound) {
      window.__fmTaskEndBound = true;
      // 兼容多种 socket 实现（socket.io / window.io）
      try {
        if (window.io && window.socket) {
          window.socket.on && window.socket.on('task_end', hideAllActivity);
        }
        // 监听原生 EventSource / WebSocket frame——直接走 MutationObserver 兜底
      } catch (e) { /* noop */ }
      // MutationObserver 兜底：检测最后一条 activity message 后没有新
      // activity message 且 chat 区域静止——认为任务完成。
      var lastActCount = -1;
      var stableSince = 0;
      new MutationObserver(function () {
        var acts = document.querySelectorAll('.fm-activity');
        if (acts.length === 0) return;
        if (acts.length !== lastActCount) {
          lastActCount = acts.length;
          stableSince = Date.now();
        } else if (Date.now() - stableSince > 3000) {
          // 3 秒 activity 数量没变化——任务完成（不再有工具调用）
          // 但要确认 chat 区域仍有新 message 出现：检查 user_message 之后
          // 是否有非 activity 的 ai-message（最终回答）
          var hasFinal = false;
          document.querySelectorAll('.ai-message').forEach(function (m) {
            if (!(m.textContent || '').indexOf('<span data-fm-act="')) hasFinal = true;
          });
          if (hasFinal) hideAllActivity();
        }
      }).observe(document.body, { childList: true, subtree: true });
    }

    // 折叠屏：默认折叠（用户主动点开看完整过程），运行中下方有 2 条 activity
    // 摘要。展开折叠栏时隐藏摘要（用户已能看到完整内容），收起时显示。
    // 找到所有折叠栏的折叠头 button（Chainlit 2.12 用 data-orientation 标记
    // Radix Accordion 容器，没有 Accordion class）。
    var foldBtns = document.querySelectorAll('button[id^="step-"][data-state]');
    foldBtns.forEach(function (b) {
      if (b.__fmFoldBlock) return;
      b.__fmFoldBlock = true;
      var item = b.closest('[data-state]');
      // 初始状态：折叠栏默认 closed——Chainlit default_open=False 透传到这里
      // 的时候 item.data-state="closed"。运行中 item 折叠时，activity 摘要显示；
      // 展开时 activity 摘要隐藏。
      function syncActivity() {
        var opened = item && item.getAttribute('data-state') === 'open';
        document.querySelectorAll('.fm-activity').forEach(function (a) {
          if (opened) {
            a.style.display = 'none';
          } else {
            a.style.display = '';
            // stale（不在最近 2 条的）始终隐藏
            if (a.classList.contains('fm-activity-stale')) a.style.display = 'none';
          }
        });
      }
      syncActivity();
      // 监听折叠头点击/键盘展开切换
      b.addEventListener('click', function (e) {
        // 延迟到 React state 更新后判断
        setTimeout(syncActivity, 50);
      }, true);
      b.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') {
          setTimeout(syncActivity, 50);
        }
      }, true);
      // 监听 data-state 变化（Radix Accordion 切展开/收起时会改 data-state）
      if (item && !item.__fmObserved) {
        item.__fmObserved = true;
        new MutationObserver(syncActivity).observe(item, { attributes: true, attributeFilter: ['data-state'] });
      }
    });
    // 折叠栏（type=run）前面那个 animate-pulse 圆形（Chainlit 给每个 step
    // 容器加的 loading skeleton）替换为 FloodMind logo。
    // 同时把正式回答（assistant_message）前面的字母 logo avatar img 隐藏——
    // 按用户偏好：品牌 logo 只在折叠栏呈现，正式回答左侧留空。
    // 折叠栏的 logo 圆形在 span.inline-block 内，正式回答的字母 logo 是
    // img[alt*="Avatar"]，两种形态都处理。
    document.querySelectorAll('[data-step-type="run"]').forEach(function (run) {
      var msg = run.querySelector('.ai-message');
      if (!msg || msg.__fmFoldLogoDone) return;
      msg.__fmFoldLogoDone = true;
      var skel = msg.querySelector(':scope > span.inline-block');
      if (skel) {
        skel.innerHTML =
          '<img src="/public/logo_dark.svg" alt="FloodMind" ' +
          'style="width:20px;height:20px;border-radius:50%;display:block;">';
      }
    });
    // 正式回答（assistant_message）前面的字母 logo img 隐藏
    document.querySelectorAll('[data-step-type="assistant_message"] img[alt*="Avatar"]').forEach(function (img) {
      if (img.__fmHidden) return;
      img.__fmHidden = true;
      // 隐藏 img 本身 + 它的 wrapper span（保留布局但取消圆形空位）
      img.style.display = 'none';
      var wrapper = img.closest('.relative.flex.shrink-0, [class*="rounded-full"]');
      if (wrapper) wrapper.style.display = 'none';
      // ai-message 取消 gap 让消息贴左
      var msg = img.closest('.ai-message');
      if (msg) msg.style.gap = '0';
    });
  }

  var obs = new MutationObserver(function () { enhance(); });
  function start() {
    enhance();
    // 顶部工具行第三个无名图标按钮 = 新建会话；补提示文案（不改布局、不加按钮）
    var btns = document.querySelectorAll('button');
    var found = [];
    for (var i = 0; i < btns.length && found.length < 3; i++) {
      var b = btns[i];
      if (!b.textContent.trim() && b.querySelector('svg')) found.push(b);
    }
    if (found.length >= 3) {
      found[2].setAttribute('title', '新建会话');
      found[2].setAttribute('aria-label', '新建会话');
      obs.disconnect();
    } else {
      obs.observe(document.body, { childList: true, subtree: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();