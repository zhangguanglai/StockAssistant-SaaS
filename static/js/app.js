const{createApp,ref,computed,watch,onMounted,onUnmounted,nextTick,h}=Vue;
const API='';
const EL={calm:'😌 冷静',confident:'😎 自信',fomo:'😰 怕错过',greedy:'🤑 贪婪',panic:'😱 恐慌',revenge:'😡 报复性',hesitant:'🤔 犹豫',impulsive:'⚡ 冲动'};

// ============================================================
// Token 管理
// ============================================================
const TOKEN_KEY='stock_token',REFRESH_KEY='stock_refresh',USER_KEY='stock_user';
function getToken(){return localStorage.getItem(TOKEN_KEY)}
function getRefreshToken(){return localStorage.getItem(REFRESH_KEY)}
function setTokens(access,refresh,username){localStorage.setItem(TOKEN_KEY,access);localStorage.setItem(REFRESH_KEY,refresh);localStorage.setItem(USER_KEY,username)}
function clearTokens(){localStorage.removeItem(TOKEN_KEY);localStorage.removeItem(REFRESH_KEY);localStorage.removeItem(USER_KEY)}
function getStoredUser(){return localStorage.getItem(USER_KEY)||''}

// 带认证的 fetch 封装
let isRefreshing=false;
let refreshPromise=null;
async function fetchWithAuth(url,options={}){
    const token=getToken();
    if(token){options.headers=options.headers||{};options.headers['Authorization']=`Bearer ${token}`}
    const res=await fetch(url,options);
    // 401 自动处理：尝试刷新 token
    if(res.status===401){
        // [UI-01 修复] 竞态条件：多个并发 401 共享同一个刷新 Promise
        if(!isRefreshing){
            isRefreshing=true;
            refreshPromise=tryRefreshToken().finally(()=>{isRefreshing=false;refreshPromise=null});
        }
        const refreshed=await refreshPromise;
        if(refreshed){
            // 用新 token 重试
            options.headers=options.headers||{};
            options.headers['Authorization']=`Bearer ${getToken()}`;
            return fetch(url,options);
        }else{
            // 刷新失败，触发登录
            clearTokens();
            if(window.__showLoginModal)window.__showLoginModal();
            return res;
        }
    }
    return res;
}

// 尝试用 refresh token 刷新 access token
async function tryRefreshToken(){
    const rt=getRefreshToken();
    if(!rt)return false;
    try{
        const res=await fetch(`${API}/api/auth/refresh`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:rt})});
        const d=await res.json();
        if(res.ok&&d.access_token){
            setTokens(d.access_token,d.refresh_token||rt,d.username||getStoredUser());
            return true;
        }
        return false;
    }catch(e){return false}
}

createApp({
setup(){
    const positions=ref([]),summary=ref({total_market_value:0,total_cost:0,total_profit:0,total_profit_pct:0,today_profit:0,position_count:0,is_trade_time:false,last_update:'-',cash:0,initial_capital:0,total_assets:0,alert_count:0});
    const loading=ref(false),searchKeyword=ref(''),sortKey=ref('market_value'),sortDir=ref('desc'),activeTab=ref('positions');
    const indexData=ref({}),tradeLogs=ref([]),tradeLogStats=ref(null),capitalForm=ref({initial:0,cash:0});
    const screenMarket=ref(null),screenStats=ref(null),screenResults=ref([]),screenHistory=ref([]);
    const screenInfo=ref({running:false,hasResult:false,lastRun:null,runTime:null});
    const watchAdding=ref(false),watchList=ref([]),watchReport=ref(null);
    const currentStrategy=ref('trend_break');
    const strategyList=ref({});
    const currentStrategyMeta=computed(()=>strategyList.value[currentStrategy.value]||null);
    const screenStrategyReason=ref('');
    const showAddModal=ref(false),showSellModal=ref(false),showDetailModal=ref(false),showAlertModal=ref(false),showImportModal=ref(false),showParamsModal=ref(false);
    const editingPosition=ref(null),detailPosition=ref(null),sellTarget=ref(null),alertTarget=ref(null);
    const emotionLabels=EL;
    const addForm=ref({ts_code:'',buy_price:'',buy_volume:'',buy_date:new Date().toISOString().slice(0,10),fee:0,note:'',reason:'',emotion:''});
    const sellForm=ref({sell_price:'',sell_volume:'',sell_date:new Date().toISOString().slice(0,10),fee:0,note:'',reason:''});
    const alertForm=ref({stop_loss:'',stop_profit:''});
    const priceLevels=ref(null),priceLevelsLoading=ref(false),priceLevelsError=ref('');
    const showAdviceModal=ref(false),adviceTarget=ref(null),adviceData=ref(null),adviceLoading=ref(false),adviceError=ref('');
    const addError=ref(''),sellError=ref(''),submitting=ref(false),submittingSell=ref(false);
    const searchResults=ref([]),importData=ref(''),importMode=ref('replace'),importError=ref('');
    const toastMsg=ref('');let toastTimer=null;

    // ============================================================
    // 市场数据状态（P1+P2）
    // ============================================================
    const marketSubTab=ref('limit');
    const limitListData=ref(null),limitListLoading=ref(false);
    const limitStepData=ref(null),limitStepLoading=ref(false);
    const limitCptData=ref(null),limitCptLoading=ref(false);
    const northboundData=ref(null),northboundLoading=ref(false);
    const topListData=ref(null),topListLoading=ref(false);
    const sectorFlowData=ref(null),sectorFlowLoading=ref(false);
    const sectorRotationData=ref(null),sectorRotationLoading=ref(false);
    const showMarketStockModal=ref(false),marketStockDetail=ref(null);
    const stockDetailSubTab=ref('finance');
    const stockFinanceData=ref(null),stockFinanceLoading=ref(false);
    const chipsData=ref(null),chipsLoading=ref(false);
    const stockNorthboundData=ref(null),stockNorthboundLoading=ref(false);
    
    // 个股财务详情弹窗
    const showStockFinanceModal=ref(false);
    const stockFinanceModalTitle=ref('');
    const stockFinanceModalCode=ref('');
    const stockFinanceSubTab=ref('indicator');
    const stockFinanceHealth=ref({score:0,level:'-',roe:0,gross:0,debt:0});

    // ============================================================
    // Auth State
    // ============================================================
    const isLoggedIn=ref(!!getToken());
    const currentUser=ref(getStoredUser()||'');
    const showAuthModal=ref(false);
    const authMode=ref('login'); // 'login' | 'register'
    const indexSource=ref('');
    const indexTime=ref('');
    const authError=ref('');
    const authSubmitting=ref(false);
    const loginForm=ref({username:'',password:''});
    const registerForm=ref({username:'',password:'',password2:'',nickname:''});

    // 暴露 showLoginModal 到全局，供 fetchWithAuth 调用
    window.__showLoginModal=()=>{showAuthModal.value=true;authMode.value='login';authError.value=''};

    async function doLogin(){
        authError.value='';const f=loginForm.value;
        if(!f.username.trim()){authError.value='请输入用户名';return}
        if(!f.password){authError.value='请输入密码';return}
        authSubmitting.value=true;
        try{
            const res=await fetch(`${API}/api/auth/login`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(f)});
            const d=await res.json();
            if(res.ok){
                setTokens(d.access_token,d.refresh_token,d.username);
                isLoggedIn.value=true;currentUser.value=d.username;
                showAuthModal.value=false;showToast(`欢迎回来，${d.nickname||d.username}`);
                loginForm.value={username:'',password:''};
                // 登录后加载数据
                await fetchData();fetchIndex();fetchCapital();
            }else{authError.value=d.error||'登录失败'}
        }catch(e){authError.value='网络错误'}finally{authSubmitting.value=false}
    }

    async function doRegister(){
        authError.value='';const f=registerForm.value;
        if(!f.username.trim()){authError.value='请输入用户名';return}
        if(f.username.trim().length<3){authError.value='用户名至少3个字符';return}
        if(!f.password||f.password.length<6){authError.value='密码至少6个字符';return}
        if(f.password!==f.password2){authError.value='两次密码不一致';return}
        authSubmitting.value=true;
        try{
            const res=await fetch(`${API}/api/auth/register`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:f.username.trim(),password:f.password,nickname:f.nickname||''})});
            const d=await res.json();
            if(res.ok){
                setTokens(d.access_token,d.refresh_token,d.username);
                isLoggedIn.value=true;currentUser.value=d.username;
                showAuthModal.value=false;showToast(`注册成功！欢迎 ${d.nickname||d.username}`);
                registerForm.value={username:'',password:'',password2:'',nickname:''};
                await fetchData();fetchIndex();fetchCapital();
            }else{authError.value=d.error||'注册失败'}
        }catch(e){authError.value='网络错误'}finally{authSubmitting.value=false}
    }

    function doLogout(){
        clearTokens();
        isLoggedIn.value=false;currentUser.value='';
        positions.value=[];summary.value={total_market_value:0,total_cost:0,total_profit:0,total_profit_pct:0,today_profit:0,position_count:0,is_trade_time:false,last_update:'-',cash:0,initial_capital:0,total_assets:0,alert_count:0};
        indexData.value={};indexSource.value='';indexTime.value='';tradeLogs.value=[];tradeLogStats.value=null;
        // [UI-02 修复] 清理所有用户敏感数据，防止切换用户后看到上一位用户数据
        screenResults.value=[];screenHistory.value=[];screenStats.value=null;screenMarket.value=null;screenInfo.value={running:false,hasResult:false,lastRun:null,runTime:null};
        watchList.value=[];watchReport.value=null;
        reviewData.value=null;
        regimeData.value=null;backtestData.value=null;alertCheckResult.value=null;
        klineData.value=null;
        compareData.value=null;compareCodes.value=[];
        spData.value=null;
        if(klineChart){klineChart.dispose();klineChart=null}
        if(compareNormChart){compareNormChart.dispose();compareNormChart=null}
        if(comparePriceChart){comparePriceChart.dispose();comparePriceChart=null}
        if(spWinRateChart){spWinRateChart.dispose();spWinRateChart=null}
        if(spAvgChgChart){spAvgChgChart.dispose();spAvgChgChart=null}
        activeTab.value='positions';
        showToast('已退出登录');
    }

    // 定时刷新 token（每小时检查一次）
    let tokenRefreshTimer=null;
    function startTokenRefresh(){
        tokenRefreshTimer=setInterval(async()=>{
            if(!getToken())return;
            const refreshed=await tryRefreshToken();
            if(refreshed){/* silent */}
        },3600000);
    }

    // Review & Strategy
    const reviewPeriod=ref('week'),reviewLoading=ref(false),reviewData=ref(null);
    const regimeLoading=ref(false),regimeData=ref(null);
    const backtestDays=ref(30),backtestHold=ref(5),backtestLoading=ref(false),backtestData=ref(null);
    const alertCheckLoading=ref(false),alertCheckResult=ref(null);
    const alertAutoMode=ref(false),alertAutoTimer=ref(null);
    // K-line
    const klineData=ref(null),klineLoading=ref(false);
    // Params
    const screenParams=ref([]);
    const paramsLoading=ref(false);
    const paramsDefault=ref([]); // 保存默认值用于重置
    const forceMode=ref(false);
    // Strategy Performance
    const spLoading=ref(false),spData=ref(null);
    let spWinRateChart=null,spAvgChgChart=null;

    function showToast(m){toastMsg.value=m;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{toastMsg.value=''},3000)}
    function round2(n){return Math.round(n*100)/100}
    function formatNum(n){if(n===null||n===undefined)return'-';return Number(n).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}

    async function fetchData(){
        if(loading.value)return;loading.value=true;
        try{const r=await fetchWithAuth(`${API}/api/positions`);const d=await r.json();positions.value=d.positions||[];summary.value={...summary.value,...d.summary};nextTick(()=>renderCharts())}catch(e){console.error(e)}finally{loading.value=false}
    }
    async function fetchIndex(){try{const r=await fetchWithAuth(`${API}/api/index`);const d=await r.json();indexData.value=d;const codes=Object.keys(d);if(codes.length>0){const first=d[codes[0]];indexSource.value=first._source||'';if(first.time){const t=first.time;indexTime.value=t.includes(' ')?t.split(' ')[1]:t}else{indexTime.value=new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}}else{indexSource.value='';indexTime.value=''}}catch(e){}}
    async function fetchTradeLog(){try{const r=await fetchWithAuth(`${API}/api/trade-log`);const d=await r.json();tradeLogs.value=d.trades||[];tradeLogStats.value=d.stats||null}catch(e){}}
    async function fetchCapital(){try{const r=await fetchWithAuth(`${API}/api/capital`);const d=await r.json();capitalForm.value=d.capital||{initial:0,cash:0}}catch(e){}}

    async function refreshData(){await Promise.all([fetchData(),fetchIndex()]);if(activeTab.value==='tradelog')await fetchTradeLog();showToast('数据已刷新')}

    let searchTimer=null;
    async function searchStock(){clearTimeout(searchTimer);const kw=addForm.value.ts_code.trim();if(kw.length<1){searchResults.value=[];return}searchTimer=setTimeout(async()=>{try{const r=await fetchWithAuth(`${API}/api/search?keyword=${encodeURIComponent(kw)}`);const d=await r.json();searchResults.value=d.results||[]}catch(e){searchResults.value=[]}},300)}
    function selectStock(s){addForm.value.ts_code=s.ts_code;searchResults.value=[]}

    function resetForm(){addForm.value={ts_code:'',buy_price:'',buy_volume:'',buy_date:new Date().toISOString().slice(0,10),fee:0,note:'',reason:'',emotion:''};editingPosition.value=null;searchResults.value=[]}
    function openAddModal(){resetForm();showAddModal.value=true}
    function openTradeModal(p){editingPosition.value=p;addForm.value={ts_code:p.ts_code,buy_price:'',buy_volume:'',buy_date:new Date().toISOString().slice(0,10),fee:0,note:'',reason:'',emotion:''};showAddModal.value=true}

    async function submitPosition(){
        addError.value='';const f=addForm.value;
        if(!f.ts_code.trim()){addError.value='请输入股票代码';return}if(!f.buy_price||f.buy_price<=0){addError.value='请输入有效的买入价格';return}if(!f.buy_volume||f.buy_volume<=0){addError.value='请输入有效的买入数量';return}
        submitting.value=true;
        try{const url=editingPosition.value?`${API}/api/positions/${editingPosition.value.id}/trades`:`${API}/api/positions`;const r=await fetchWithAuth(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(f)});const d=await r.json();if(r.ok){showAddModal.value=false;showToast(d.message||'保存成功');resetForm();await fetchData()}else{addError.value=d.error||'保存失败'}}catch(e){addError.value='网络错误'}finally{submitting.value=false}
    }

    function openSellModal(p){sellTarget.value=p;sellForm.value={sell_price:p.current_price||'',sell_volume:'',sell_date:new Date().toISOString().slice(0,10),fee:0,note:'',reason:''};sellError.value='';showSellModal.value=true}
    const sellPreview=computed(()=>{const f=sellForm.value,t=sellTarget.value;if(!t||!f.sell_price||!f.sell_volume)return null;const ca=t.avg_cost,sa=f.sell_price*f.sell_volume,co=ca*f.sell_volume,pr=sa-co-(f.fee||0);return{profit:Math.round(pr*100)/100,profit_pct:co>0?Math.round(pr/co*100*100)/100:0}});
    async function submitSell(){
        sellError.value='';const f=sellForm.value;if(!f.sell_price||f.sell_price<=0){sellError.value='请输入卖出价格';return}if(!f.sell_volume||f.sell_volume<=0){sellError.value='请输入卖出数量';return}
        submittingSell.value=true;
        try{const r=await fetchWithAuth(`${API}/api/positions/${sellTarget.value.id}/sell`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(f)});const d=await r.json();if(r.ok){showSellModal.value=false;showToast(`${d.message}，盈亏 ${d.sell_profit>=0?'+':''}¥${d.sell_profit.toFixed(2)}`);await fetchData()}else{sellError.value=d.error||'卖出失败'}}catch(e){sellError.value='网络错误'}finally{submittingSell.value=false}
    }

    function openAlertModal(p){
        alertTarget.value=p;
        alertForm.value={stop_loss:p.stop_loss||'',stop_profit:p.stop_profit||''};
        priceLevels.value=null;priceLevelsLoading.value=true;priceLevelsError.value='';
        showAlertModal.value=true;
        fetchWithAuth(`${API}/api/positions/${p.id}/levels`)
            .then(r=>r.json())
            .then(d=>{if(d.error){priceLevelsError.value=d.error}else{priceLevels.value=d}})
            .catch(()=>{priceLevelsError.value='获取K线数据失败'})
            .finally(()=>{priceLevelsLoading.value=false});
    }
    async function submitAlerts(){try{const r=await fetchWithAuth(`${API}/api/positions/${alertTarget.value.id}/alerts`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(alertForm.value)});if(r.ok){showAlertModal.value=false;showToast('止损止盈已设置');await fetchData()}}catch(e){showToast('设置失败')}}
    async function clearAlerts(){alertForm.value={stop_loss:'',stop_profit:''};try{const r=await fetchWithAuth(`${API}/api/positions/${alertTarget.value.id}/alerts`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({stop_loss:null,stop_profit:null})});if(r.ok){showAlertModal.value=false;showToast('止损止盈已清除');await fetchData()}}catch(e){showToast('操作失败')}}

    function openDetailModal(p){detailPosition.value=p;showDetailModal.value=true;loadKline(p.ts_code)}
    function closeDetailModal(){showDetailModal.value=false;if(klineChart){klineChart.dispose();klineChart=null}}
    function openAdviceModal(p){
        adviceTarget.value=p;adviceData.value=null;adviceLoading.value=true;adviceError.value='';
        showAdviceModal.value=true;
        fetchWithAuth(`${API}/api/positions/${p.id}/advice`)
            .then(r=>r.json()).then(d=>{if(d.error){adviceError.value=d.error}else{adviceData.value=d}})
            .catch(()=>{adviceError.value='获取策略建议失败'}).finally(()=>{adviceLoading.value=false});
    }
    function quickAddFromAdvice(s){
        showAdviceModal.value=false;
        openAddModal();addForm.value.ts_code=adviceTarget.value.ts_code;addForm.value.buy_price=s.price;addForm.value.buy_volume=s.volume;
        showToast(`已填入加仓参数：${s.volume}股 @ ¥${s.price}`);
    }
    function quickSellFromAdvice(s){
        showAdviceModal.value=false;
        openSellModal(adviceTarget.value);sellForm.value.sell_price=s.price;sellForm.value.sell_volume=Math.min(s.volume,adviceTarget.value.total_volume);
        showToast(`已填入减仓参数：${sellForm.value.sell_volume}股 @ ¥${s.price}`);
    }
    async function confirmDelete(p){if(!confirm(`确认删除 ${p.name}(${p.ts_code}) 的持仓？`))return;try{const r=await fetchWithAuth(`${API}/api/positions/${p.id}`,{method:'DELETE'});if(r.ok){showToast('持仓已删除');await fetchData()}}catch(e){showToast('删除失败')}}
    async function deleteTrade(tid){if(!detailPosition.value)return;if(!confirm('确认删除该笔交易记录？'))return;try{const r=await fetchWithAuth(`${API}/api/positions/${detailPosition.value.id}/trades/${tid}`,{method:'DELETE'});if(r.ok){showToast('交易记录已删除');await fetchData();const u=positions.value.find(p=>p.id===detailPosition.value.id);if(u)detailPosition.value=u}}catch(e){showToast('删除失败')}}

    function saveCapital(){fetchWithAuth(`${API}/api/capital`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(capitalForm.value)}).then(r=>r.json()).then(d=>{if(d.message){showToast(d.message);fetchData()}}).catch(()=>showToast('保存失败'))}

    function switchToTradeLog(){activeTab.value='tradelog';fetchTradeLog();fetchCapital();nextTick(()=>renderAnalysisCharts())}
    function switchToAnalysis(){activeTab.value='analysis';fetchTradeLog();nextTick(()=>renderAnalysisCharts())}
    function switchToScreener(){activeTab.value='screener';loadScreenResult()}
    function switchToReview(){activeTab.value='review';loadReview(reviewPeriod.value)}
    function switchToStrategy(){activeTab.value='strategy';loadRegime();loadStrategyPerformance()}

    // === Review Functions ===
    async function loadReview(period){
        reviewPeriod.value=period;reviewLoading.value=true;reviewData.value=null;
        try{
            const r=await fetchWithAuth(`${API}/api/review/${period}`);const d=await r.json();
            if(d.error){showToast(d.error);return}
            reviewData.value=d;
        }catch(e){showToast('加载复盘数据失败')}finally{reviewLoading.value=false}
    }

    // === Regime Functions ===
    async function loadRegime(){
        regimeLoading.value=true;regimeData.value=null;
        try{
            const r=await fetchWithAuth(`${API}/api/market-regime`);const d=await r.json();
            if(d.error){showToast(d.error);return}
            regimeData.value=d;
        }catch(e){showToast('加载市场环境失败')}finally{regimeLoading.value=false}
    }

    // === Backtest Functions ===
    async function runBacktest(){
        backtestLoading.value=true;backtestData.value=null;
        try{
            const r=await fetchWithAuth(`${API}/api/backtest?days=${backtestDays.value}&hold=${backtestHold.value}`);const d=await r.json();
            if(d.error){showToast(d.error);return}
            backtestData.value=d;
        }catch(e){showToast('回测请求失败')}finally{backtestLoading.value=false}
    }

    // === Alert Check Functions ===
    async function checkAlerts(silent=false){
        alertCheckLoading.value=true;if(!silent)alertCheckResult.value=null;
        try{
            const r=await fetchWithAuth(`${API}/api/alerts/check`);const d=await r.json();
            if(d.error){if(!silent)showToast(d.error);return}
            alertCheckResult.value=d;
            // 触发预警时发送浏览器通知
            if(d.total>0&&d.triggered){
                if('Notification' in window){
                    if(Notification.permission==='default'){await Notification.requestPermission()}
                    if(Notification.permission==='granted'){
                        d.triggered.forEach(a=>{
                            const n=new Notification(`🚨 ${a.level==='danger'?'止损':'止盈'}预警`,{body:a.message,icon:'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">'+(a.level==='danger'?'📉':'📈')+'</text></svg>'});
                            n.onclick=()=>{window.focus();n.close()};
                        });
                    }
                }
            }
        }catch(e){if(!silent)showToast('预警检查失败')}finally{alertCheckLoading.value=false}
    }
    function toggleAlertAuto(){
        if(alertAutoMode.value){
            // 启动自动检查
            if('Notification' in window&&Notification.permission==='default'){Notification.requestPermission()}
            checkAlerts(true);
            alertAutoTimer.value=setInterval(()=>checkAlerts(true),60000);
            showToast('预警自动检查已开启（每60秒）');
        }else{
            if(alertAutoTimer.value){clearInterval(alertAutoTimer.value);alertAutoTimer.value=null}
            showToast('预警自动检查已关闭');
        }
    }

    // === Strategy Performance Functions ===
    async function loadStrategyPerformance(){
        spLoading.value=true;spData.value=null;
        try{
            const r=await fetchWithAuth(`${API}/api/strategy-performance`);const d=await r.json();
            if(d.error){showToast(d.error);return}
            spData.value=d;
            nextTick(()=>renderSPCharts());
        }catch(e){showToast('加载策略效果失败')}finally{spLoading.value=false}
    }
    function renderSPCharts(){
        if(!spData.value)return;
        const strategies=spData.value.strategies||{};
        const keys=Object.keys(strategies);
        if(keys.length===0)return;
        const names=keys.map(k=>strategies[k].display_name||k);
        const winRates=keys.map(k=>strategies[k].win_rate||0);
        const avgChgs=keys.map(k=>strategies[k].avg_chg||0);
        const colors=winRates.map(v=>v>=50?'#ef4444':'#22c55e');
        const chgColors=avgChgs.map(v=>v>=0?'#ef4444':'#22c55e');
        // Win rate chart
        const el1=document.getElementById('spWinRateChart');
        if(el1){
            if(spWinRateChart)spWinRateChart.dispose();
            spWinRateChart=echarts.init(el1,'dark');
            spWinRateChart.setOption({
                backgroundColor:'transparent',
                tooltip:{trigger:'axis',valueFormatter:v=>v!=null?v+'%':'-'},
                grid:{left:50,right:20,top:10,bottom:30},
                xAxis:{type:'category',data:names,axisLabel:{color:'#8b8fa3',fontSize:11},axisLine:{lineStyle:{color:'#2a2e3f'}},splitLine:{show:false}},
                yAxis:{type:'value',max:100,axisLabel:{color:'#8b8fa3',fontSize:10,formatter:v=>v+'%'},splitLine:{lineStyle:{color:'#1e2130'}},axisLine:{show:false}},
                series:[{type:'bar',data:winRates.map((v,i)=>({value:v,itemStyle:{color:colors[i],borderRadius:[4,4,0,0]}})),barMaxWidth:50,label:{show:true,position:'top',color:'#8b8fa3',fontSize:11,formatter:'{c}%'}}]
            });
        }
        // Avg change chart
        const el2=document.getElementById('spAvgChgChart');
        if(el2){
            if(spAvgChgChart)spAvgChgChart.dispose();
            spAvgChgChart=echarts.init(el2,'dark');
            spAvgChgChart.setOption({
                backgroundColor:'transparent',
                tooltip:{trigger:'axis',valueFormatter:v=>v!=null?(v>=0?'+':'')+v+'%':'-'},
                grid:{left:50,right:20,top:10,bottom:30},
                xAxis:{type:'category',data:names,axisLabel:{color:'#8b8fa3',fontSize:11},axisLine:{lineStyle:{color:'#2a2e3f'}},splitLine:{show:false}},
                yAxis:{type:'value',axisLabel:{color:'#8b8fa3',fontSize:10,formatter:v=>(v>=0?'+':'')+v+'%'},splitLine:{lineStyle:{color:'#1e2130'}},axisLine:{show:false}},
                series:[{type:'bar',data:avgChgs.map((v,i)=>({value:v,itemStyle:{color:chgColors[i],borderRadius:[4,4,0,0]}})),barMaxWidth:50,label:{show:true,position:'top',color:'#8b8fa3',fontSize:11,formatter:p=>(p.value>=0?'+':'')+p.value+'%'}}]
            });
        }
    }

    // === K-line Functions ===
    async function loadKline(tsCode){
        klineLoading.value=true;klineData.value=null;
        try{
            const r=await fetchWithAuth(`${API}/api/kline/${tsCode}`);const d=await r.json();
            if(d.error){klineData.value=null;return}
            klineData.value=d;
            nextTick(()=>renderKlineChart());
        }catch(e){klineData.value=null}finally{klineLoading.value=false}
    }
    let klineChart=null;
    function renderKlineChart(){
        const el=document.getElementById('klineChart');
        if(!el||!klineData.value)return;
        if(!klineChart)klineChart=echarts.init(el,'dark');
        // API returns: dates[], klines[[o,c,l,h],...], ma5[], ma20[], volumes[]
        const dates=klineData.value.dates||[];
        const klines=klineData.value.klines||[];
        const ma5=klineData.value.ma5||[];
        const ma20=klineData.value.ma20||[];
        const volumes=klineData.value.volumes||[];
        if(dates.length===0)return;
        const upColor='#ef4444',downColor='#22c55e';
        // Build tooltip data for lookup
        const dayData=dates.map((d,i)=>{
            const k=klines[i]||[0,0,0,0];
            return{date:d,open:k[0],close:k[1],low:k[2],high:k[3],vol:volumes[i]||0,ma5:ma5[i],ma20:ma20[i]};
        });
        klineChart.setOption({
            backgroundColor:'transparent',
            tooltip:{trigger:'axis',axisPointer:{type:'cross'},formatter:function(params){
                const idx=params[0]?.dataIndex;if(idx===undefined||!dayData[idx])return'';
                const d=dayData[idx];
                return `<b>${d.date}</b><br/>开: ¥${d.open}<br/>收: ¥${d.close}<br/>低: ¥${d.low}<br/>高: ¥${d.high}<br/>量: ${d.vol}手<br/>MA5: ${d.ma5!=null?'¥'+d.ma5:'-'}}<br/>MA20: ${d.ma20!=null?'¥'+d.ma20:'-'}`;
            }},
            legend:{data:['K线','MA5','MA20'],top:0,textStyle:{color:'#8b8fa3',fontSize:11}},
            grid:[{left:60,right:20,top:30,height:'55%'},{left:60,right:20,top:'75%',height:'15%'}],
            xAxis:[{type:'category',data:dates,gridIndex:0,axisLabel:{color:'#8b8fa3',fontSize:10},axisLine:{lineStyle:{color:'#2a2e3f'}},splitLine:{show:false}},{type:'category',data:dates,gridIndex:1,axisLabel:{show:false},axisLine:{lineStyle:{color:'#2a2e3f'}}}],
            yAxis:[{type:'value',gridIndex:0,scale:true,splitNumber:4,axisLabel:{color:'#8b8fa3',fontSize:10,formatter:v=>'¥'+v.toFixed(2)},splitLine:{lineStyle:{color:'#1e2130'}},axisLine:{show:false}},{type:'value',gridIndex:1,scale:true,splitNumber:2,axisLabel:{color:'#8b8fa3',fontSize:9},splitLine:{show:false},axisLine:{show:false}}],
            dataZoom:[{type:'inside',xAxisIndex:[0,1],start:60,end:100},{type:'slider',xAxisIndex:[0,1],start:60,end:100,bottom:2,height:16,textStyle:{color:'#8b8fa3',fontSize:10},borderColor:'#2a2e3f',fillerColor:'rgba(59,130,246,0.15)',handleStyle:{color:'#3b82f6'}}],
            series:[
                {name:'K线',type:'candlestick',xAxisIndex:0,yAxisIndex:0,data:klines,itemStyle:{color:upColor,color0:downColor,borderColor:upColor,borderColor0:downColor}},
                {name:'MA5',type:'line',xAxisIndex:0,yAxisIndex:0,data:ma5,smooth:true,lineStyle:{width:1,color:'#f59e0b'},symbol:'none',connectNulls:true},
                {name:'MA20',type:'line',xAxisIndex:0,yAxisIndex:0,data:ma20,smooth:true,lineStyle:{width:1,color:'#3b82f6'},symbol:'none',connectNulls:true},
                {type:'bar',xAxisIndex:1,yAxisIndex:1,data:volumes.map((v,i)=>({value:v,itemStyle:{color:klines[i]&&klines[i][1]>=klines[i][0]?upColor:downColor}})),barMaxWidth:8}
            ]
        });
    }

    // Screener
    let screenPollTimer=null;
    async function loadScreenParams(){
        paramsLoading.value=true;
        try{
            const r=await fetchWithAuth(`${API}/api/screen/params?strategy=${currentStrategy.value}`);
            const d=await r.json();
            // 后端可能返回 {params: [...]}（指定strategy时）或 {params: {strategy: [...]}}（无strategy时）
            let raw=null;
            if(d.params){
                if(Array.isArray(d.params)){
                    raw=d.params;
                }else if(d.params[currentStrategy.value]){
                    raw=d.params[currentStrategy.value];
                }
            }
            if(raw&&raw.length>0){
                screenParams.value=JSON.parse(JSON.stringify(raw));
                paramsDefault.value=JSON.parse(JSON.stringify(raw));
            }else{
                screenParams.value=[];
                paramsDefault.value=[];
            }
        }catch(e){screenParams.value=[];paramsDefault.value=[]}
        finally{paramsLoading.value=false}
    }
    function resetParams(){
        screenParams.value=JSON.parse(JSON.stringify(paramsDefault.value));
    }
    function onParamChange(p){
        // 确保 value 在 min/max 范围内
        if(p.value<p.min)p.value=p.min;
        if(p.value>p.max)p.value=p.max;
    }
    async function openParamsModal(force=false){
        forceMode.value=force;
        showParamsModal.value=true;
        await loadScreenParams();
    }
    async function confirmRunScreen(){
        showParamsModal.value=false;
        // 构建参数数组
        const params=screenParams.value.map(p=>({key:p.key,value:p.value}));
        // 判断是否有修改（与默认值不同）
        const changed=params.filter((p,i)=>{
            const def=paramsDefault.value[i];
            return def&&p.value!==def.value;
        });
        try{
            const body={top_n:20,strategy:currentStrategy.value};
            if(forceMode.value)body.force=true;
            if(changed.length>0)body.params=changed;
            const r=await fetchWithAuth(`${API}/api/screen/run`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
            const d=await r.json();
            if(d.error){showToast(d.error);return}
            screenInfo.value={running:true,hasResult:false,lastRun:null,runTime:null};
            screenPollTimer=setInterval(pollScreenResult,5000);
            const changedMsg=changed.length>0?`（已调整 ${changed.length} 个参数）`:'';
            showToast(`${currentStrategyMeta.value?currentStrategyMeta.value.name:'选股'}已启动${changedMsg}`);
        }catch(e){showToast('启动选股失败')}
    }
    async function startScreen(){
        openParamsModal(false);
    }
    async function startScreenForce(){
        openParamsModal(true);
    }
    async function pollScreenResult(){
        try{
            const r=await fetchWithAuth(`${API}/api/screen/status?strategy=${currentStrategy.value}`);const d=await r.json();
            if(!d.running){
                clearInterval(screenPollTimer);screenPollTimer=null;
                loadScreenResult();
            }
        }catch(e){}
    }
    async function loadScreenResult(){
        try{
            const r=await fetchWithAuth(`${API}/api/screen/result?strategy=${currentStrategy.value}`);const d=await r.json();
            screenMarket.value=d.market||null;screenStats.value=d.stats||null;screenResults.value=d.results||[];
            screenHistory.value=d.history||[];
            screenInfo.value={running:d.running||false,hasResult:!!d.results&&d.results.length>0,lastRun:d.screen_time||null,runTime:d.run_time||null};
            if(d.error)showToast('选股出错: '+d.error);
        }catch(e){}
    }
    async function loadStrategies(){
        try{
            const r=await fetchWithAuth(`${API}/api/screen/strategies`);const d=await r.json();
            if(d.strategies){
                strategyList.value=d.strategies;
                // 根据大盘环境自动设置推荐策略
                if(d.recommended&&d.reason){
                    currentStrategy.value=d.recommended;
                    screenStrategyReason.value=d.reason;
                }
            }
        }catch(e){}
    }
    loadStrategies();
    const screenBonusTags=computed(()=>{const tags=new Set();screenResults.value.forEach(r=>{(r.bonus_details||[]).forEach(t=>tags.add(t))});return [...tags]});
    function quickBuyFromScreen(r){resetForm();addForm.value.ts_code=r.ts_code;addForm.value.buy_price=r.price;showAddModal.value=true}

    // 观察池
    async function addToWatch(r){
        try{
            const res=await fetchWithAuth(`${API}/api/watch-list`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ts_code:r.ts_code,name:r.name,price:r.price,strategy:currentStrategy.value,total_score:r.total_score})});
            const d=await res.json();
            showToast(d.message||'操作完成');
        }catch(e){showToast('添加失败')}
    }
    async function addAllToWatch(){
        if(!screenResults.value.length)return;
        if(watchAdding.value)return; // 防止重复点击
        watchAdding.value=true;
        try{
            // 只加入Top 10到观察池
            const stocks=screenResults.value.slice(0,10).map(r=>({ts_code:r.ts_code,name:r.name,price:r.price,strategy:currentStrategy.value,total_score:r.total_score}));
            const res=await fetchWithAuth(`${API}/api/watch-list/batch`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stocks})});
            const d=await res.json();
            // 显示更详细的提示：本次添加数量 / 观察池总数量
            const msg = d.message || `已加入 ${d.added || 0} 只到观察池`;
            showToast(`${msg} (观察池共${d.count || 0}只)`);
        }catch(e){showToast('批量添加失败');console.error('[addAllToWatch] 错误:', e)}
        finally{watchAdding.value=false}
    }
    async function showWatchReport(){
        try{
            const res=await fetchWithAuth(`${API}/api/watch-list/report`);
            const d=await res.json();
            watchReportData.value=d.report;
            if(!d.report){showToast(d.message||'观察池为空');return}
            alert(`📊 观察池跟踪日报\n${'='.repeat(40)}\n生成时间：${d.report.generated_at}\n${d.summary}\n\n`+d.report.items.map(i=>`${i.status==='盈利'?'🔴':i.status==='亏损'?'🟢':'⚪'} ${i.name||i.ts_code} ${i.add_price}→${i.current_price} ${i.chg_pct>=0?'+':''}${i.chg_pct}% (${i.status})`).join('\n'));
        }catch(e){showToast('获取日报失败')}
    }

    // === Watch List Tab Functions ===
    const watchItems=ref([]),watchLoading=ref(false),watchListCount=ref(0);
    const watchSearch=ref(''),watchFilter=ref('all');
    const watchReportData=ref(null);

    function translateStrategy(code){
        const map={'trend_break':'趋势突破','sector_leader':'板块龙头','oversold_bounce':'超跌反弹'};
        return map[code]||code||'-';
    }
    const showWatchAdviceModal=ref(false),watchAdviceTarget=ref(null),watchAdviceData=ref(null),watchAdviceLoading=ref(false),watchAdviceError=ref('');
    const showWatchStrategyModal=ref(false),watchStrategyTarget=ref(null),watchStrategyData=ref(null),watchStrategyLoading=ref(false),watchStrategyError=ref('');
    const watchAddModal=ref(false),watchAddForm=ref({ts_code:'',tag:'',note:''}),watchAddSearch=ref([]);
    const watchTagModal=ref(false),watchTagTarget=ref(null),watchTagForm=ref({tag:''});
    const watchNoteModal=ref(false),watchNoteTarget=ref(null),watchNoteForm=ref({note:''});
    const watchStats=computed(()=>{
        const items=watchItems.value;let profit=0,loss=0,total=0;
        items.forEach(w=>{const c=w.track_chg_pct;if(c==null||c===undefined)return;total+=c;if(c>0)profit++;else if(c<0)loss++});
        return{profit,loss,avg_chg:items.length>0?Math.round(total/items.length*100)/100:0};
    });
    const watchGroups=computed(()=>{const s=new Set();watchItems.value.forEach(w=>{if(w.tag)s.add(w.tag)});return[...s].sort()});
    const filteredWatchItems=computed(()=>{
        let list=[...watchItems.value];
        if(watchFilter.value!=='all')list=list.filter(w=>w.tag===watchFilter.value);
        if(watchSearch.value.trim()){const kw=watchSearch.value.trim().toLowerCase();list=list.filter(w=>(w.name||'').toLowerCase().includes(kw)||w.ts_code.toLowerCase().includes(kw)||(w.tag||'').toLowerCase().includes(kw))}
        return list;
    });

    function switchToWatch(){activeTab.value='watch';loadWatchList()}
    async function loadWatchList(){
        watchLoading.value=true;
        try{
            const r=await fetchWithAuth(`${API}/api/watch-list`);const d=await r.json();
            watchItems.value=d.items||[];watchListCount.value=d.count||0;
        }catch(e){watchItems.value=[]}finally{watchLoading.value=false}
    }
    async function removeWatchItem(w){
        if(!confirm(`确认移除 ${w.name||w.ts_code}？`))return;
        try{
            const r=await fetchWithAuth(`${API}/api/watch-list/${w.ts_code}`,{method:'DELETE'});
            const d=await r.json();showToast(d.message);await loadWatchList();
        }catch(e){showToast('移除失败')}
    }
    async function clearWatchList(){
        if(!confirm('确认清空整个观察池？此操作不可撤销！'))return;
        try{
            const r=await fetchWithAuth(`${API}/api/watch-list/clear`,{method:'DELETE'});
            const d=await r.json();showToast(d.message);await loadWatchList();watchReportData.value=null;
        }catch(e){showToast('清空失败')}
    }
    async function loadWatchReport(){
        try{
            const r=await fetchWithAuth(`${API}/api/watch-list/report`);const d=await r.json();
            if(d.report){watchReportData.value=d.report}else{showToast(d.message||'观察池为空')}
        }catch(e){showToast('获取日报失败')}
    }
    // 手动添加到观察池
    let watchSearchTimer=null;
    async function searchWatchStock(){
        clearTimeout(watchSearchTimer);const kw=watchAddForm.value.ts_code.trim();
        if(kw.length<1){watchAddSearch.value=[];return}
        watchSearchTimer=setTimeout(async()=>{try{const r=await fetchWithAuth(`${API}/api/search?keyword=${encodeURIComponent(kw)}`);const d=await r.json();watchAddSearch.value=d.results||[]}catch(e){watchAddSearch.value=[]}},300);
    }
    function selectWatchStock(s){watchAddForm.value.ts_code=s.ts_code;watchAddSearch.value=[]}
    async function submitWatchAdd(){
        const f=watchAddForm.value;if(!f.ts_code.trim()){showToast('请输入股票代码');return}
        try{
            const r=await fetchWithAuth(`${API}/api/search?keyword=${encodeURIComponent(f.ts_code)}`);
            const d=await r.json();
            let name='',price=0;
            if(d.results&&d.results.length>0){const s=d.results.find(x=>x.ts_code===f.ts_code.trim())||d.results[0];name=s.name;price=s.price||0}
            const res=await fetchWithAuth(`${API}/api/watch-list`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ts_code:f.ts_code.trim(),name,price,note:f.note,tag:f.tag})});
            const rd=await res.json();showToast(rd.message||'已添加');
            watchAddModal.value=false;watchAddForm.value={ts_code:'',tag:'',note:''};await loadWatchList();
        }catch(e){showToast('添加失败')}
    }
    function quickBuyFromWatch(w){
        resetForm();addForm.value.ts_code=w.ts_code;addForm.value.buy_price=w.current_price||w.add_price;showAddModal.value=true;
    }
    function openWatchTagModal(w){watchTagTarget.value=w;watchTagForm.value={tag:w.tag||''};watchTagModal.value=true}
    async function submitWatchTag(){
        try{
            const r=await fetchWithAuth(`${API}/api/watch-list/${watchTagTarget.value.ts_code}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(watchTagForm.value)});
            const d=await r.json();showToast(d.message);watchTagModal.value=false;await loadWatchList();
        }catch(e){showToast('保存失败')}
    }
    function openWatchNoteModal(w){watchNoteTarget.value=w;watchNoteForm.value={note:w.note||''};watchNoteModal.value=true}
    async function submitWatchNote(){
        try{
            const r=await fetchWithAuth(`${API}/api/watch-list/${watchNoteTarget.value.ts_code}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(watchNoteForm.value)});
            const d=await r.json();showToast(d.message);watchNoteModal.value=false;await loadWatchList();
        }catch(e){showToast('保存失败')}
    }
    function openAdviceModalForWatch(w){
        watchAdviceTarget.value=w;watchAdviceData.value=null;watchAdviceLoading.value=true;watchAdviceError.value='';
        showWatchAdviceModal.value=true;
        fetchWithAuth(`${API}/api/watch-list/${w.ts_code}/advice`)
            .then(r=>r.json()).then(d=>{if(d.error){watchAdviceError.value=d.error}else{watchAdviceData.value=d}})
            .catch(()=>{watchAdviceError.value='获取策略建议失败'}).finally(()=>{watchAdviceLoading.value=false});
    }
    function openWatchStrategyModal(w){
        // 打开选股策略买入建议弹窗（区分于持仓操作建议）
        if(!w||!w.ts_code){console.error('openWatchStrategyModal: invalid watch item',w);return;}
        watchStrategyTarget.value=w;watchStrategyData.value=null;watchStrategyLoading.value=true;watchStrategyError.value='';
        showWatchStrategyModal.value=true;
        fetchWithAuth(`${API}/api/watch-list/${w.ts_code}/strategy-advice`)
            .then(r=>r.json()).then(d=>{if(d.error){watchStrategyError.value=d.error}else{watchStrategyData.value=d}})
            .catch(()=>{watchStrategyError.value='获取策略建议失败'}).finally(()=>{watchStrategyLoading.value=false});
    }
    function openKlineModalForWatch(w){
        detailPosition.value={ts_code:w.ts_code,name:w.name||w.ts_code};
        showDetailModal.value=true;
        loadKline(w.ts_code);
    }

    // Import/Export
    function handleExport(){fetchWithAuth(`${API}/api/export`).then(r=>r.json()).then(data=>{const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`portfolio_${new Date().toISOString().slice(0,10)}.json`;a.click();URL.revokeObjectURL(url);showToast('导出成功')})}

    // === Compare Functions ===
    const compareCodes=ref([]),compareInput=ref(''),compareDays=ref(60);
    const compareData=ref(null),compareLoading=ref(false),compareSearchResults=ref([]);
    const compareColors=['#ef4444','#3b82f6','#22c55e','#f59e0b'];
    // ============================================================
    // P1+P2: 市场数据
    // ============================================================

    function switchToMarket(){activeTab.value='market';loadLimitList()}

    async function loadLimitList(){
        limitListLoading.value=true;limitStepLoading.value=true;limitCptLoading.value=true;
        try{
            const [r1,r2,r3]=await Promise.all([
                fetchWithAuth(`${API}/api/market/limit-list`),
                fetchWithAuth(`${API}/api/market/limit-step`),
                fetchWithAuth(`${API}/api/market/limit-cpt`),
            ]);
            limitListData.value=await r1.json();
            limitStepData.value=await r2.json();
            limitCptData.value=await r3.json();
        }catch(e){console.error('涨停数据加载失败',e)}
        finally{limitListLoading.value=false;limitStepLoading.value=false;limitCptLoading.value=false}
    }

    async function loadNorthbound(){
        northboundLoading.value=true;
        try{
            const r=await fetchWithAuth(`${API}/api/market/northbound-flow?days=30`);
            northboundData.value=await r.json();
            nextTick(()=>renderNorthboundChart());
        }catch(e){console.error('北向资金加载失败',e)}
        finally{northboundLoading.value=false}
    }

    function renderNorthboundChart(){
        const el=document.getElementById('northboundChart');
        if(!el||!northboundData.value?.data)return;
        const data=northboundData.value.data.reverse();
        const dates=data.map(d=>d.trade_date?.slice(4,6)+'/'+d.trade_date?.slice(6,8)||'');
        const northMoney=data.map(d=>parseFloat(d.north_money)||0);
        const hgt=data.map(d=>parseFloat(d.hgt)||0);
        const sgt=data.map(d=>parseFloat(d.sgt)||0);
        if(window._northboundChart)window._northboundChart.dispose();
        const chart=echarts.init(el);
        window._northboundChart=chart;
        chart.setOption({
            tooltip:{trigger:'axis',valueFormatter:v=>(v/10000).toFixed(0)+'亿'},
            legend:{data:['北向资金','沪股通','深股通'],textStyle:{color:'#9ca3af',fontSize:11},top:0},
            grid:{top:35,left:60,right:15,bottom:25},
            xAxis:{type:'category',data:dates,axisLabel:{color:'#9ca3af',fontSize:10}},
            yAxis:{type:'value',axisLabel:{color:'#9ca3af',fontSize:10,formatter:v=>(v/10000).toFixed(0)+'亿'}},
            series:[
                {name:'北向资金',type:'bar',data:northMoney,itemStyle:{color:p=>p.value>=0?'#ef4444':'#22c55e'}},
                {name:'沪股通',type:'line',data:hgt,lineStyle:{color:'#f59e0b',width:1.5},symbol:'none'},
                {name:'深股通',type:'line',data:sgt,lineStyle:{color:'#3b82f6',width:1.5},symbol:'none'},
            ]
        });
    }

    function getNorthboundColor(){
        // 根据 north_money 和 hgt/sgt 的差值判断
        const d=northboundData.value?.latest;
        if(!d)return 'text-red';
        const hgt=parseFloat(d.hgt)||0;
        const sgt=parseFloat(d.sgt)||0;
        // hgt > sgt 表示北向净流入
        return hgt>sgt?'text-red':'text-green';
    }

    async function loadTopList(){
        topListLoading.value=true;
        try{
            const r=await fetchWithAuth(`${API}/api/market/top-list`);
            topListData.value=await r.json();
        }catch(e){console.error('龙虎榜加载失败',e)}
        finally{topListLoading.value=false}
    }

    async function loadSectorFlow(){
        sectorFlowLoading.value=true;
        try{
            const r=await fetchWithAuth(`${API}/api/market/sector-flow`);
            sectorFlowData.value=await r.json();
        }catch(e){console.error('板块资金流加载失败',e)}
        finally{sectorFlowLoading.value=false}
    }

    async function loadSectorRotation(){
        sectorRotationLoading.value=true;
        try{
            const r=await fetchWithAuth(`${API}/api/market/sector-rotation`);
            sectorRotationData.value=await r.json();
        }catch(e){console.error('板块轮动加载失败',e)}
        finally{sectorRotationLoading.value=false}
    }

    async function openMarketStockDetail(ts_code){
        marketStockDetail.value={ts_code};
        stockDetailSubTab.value='finance';
        stockFinanceLoading.value=true;
        showMarketStockModal.value=true;
        // 加载K线
        nextTick(async()=>{
            try{
                const kr=await fetchWithAuth(`${API}/api/kline/${ts_code}`);
                const kd=await kr.json();
                renderMarketStockKline(kd);
            }catch(e){}
        });
        // 加载财务数据
        try{
            const fr=await fetchWithAuth(`${API}/api/stock/${ts_code}/finance`);
            stockFinanceData.value=await fr.json();
        }catch(e){stockFinanceData.value=null}
        finally{stockFinanceLoading.value=false}
    }

    // 打开个股财务详情弹窗（持仓/自选使用）
    async function openStockFinanceModal(ts_code,name){
        stockFinanceModalCode.value=ts_code;
        stockFinanceModalTitle.value=name||ts_code;
        stockFinanceSubTab.value='indicator';
        stockFinanceLoading.value=true;
        showStockFinanceModal.value=true;
        
        try{
            const fr=await fetchWithAuth(`${API}/api/stock/${ts_code}/finance`);
            const data=await fr.json();
            stockFinanceData.value=data;
            // 计算财务健康评分
            calculateFinanceHealth(data);
            // 如果切换到营收趋势tab，渲染图表
            nextTick(()=>{
                if(stockFinanceSubTab.value==='income'&&data?.income_trend?.length){
                    renderStockIncomeChart(data.income_trend);
                }
            });
        }catch(e){
            stockFinanceData.value=null;
            stockFinanceHealth.value={score:0,level:'-',roe:0,gross:0,debt:0};
        }finally{
            stockFinanceLoading.value=false;
        }
    }
    
    // 计算财务健康评分
    function calculateFinanceHealth(data){
        const fina=data?.fina_indicator?.[0];
        if(!fina){
            stockFinanceHealth.value={score:0,level:'无数据',roe:0,gross:0,debt:0};
            return;
        }
        
        const roe=(fina.roe||0)*100;
        const gross=(fina.grossprofit_margin||0)*100;
        const net=(fina.netprofit_margin||0)*100;
        const debt=(fina.debt_to_assets||0)*100;
        
        // 评分逻辑（满分100）
        let score=0;
        // ROE评分（0-30分）
        score+=roe>=20?30:roe>=15?25:roe>=10?20:roe>=5?10:roe>0?5:0;
        // 毛利率评分（0-25分）
        score+=gross>=40?25:gross>=30?20:gross>=20?15:gross>=10?10:gross>0?5:0;
        // 净利率评分（0-20分）
        score+=net>=20?20:net>=15?15:net>=10?10:net>=5?5:net>0?2:0;
        // 负债率评分（0-25分，越低越好）
        score+=debt<=30?25:debt<=40?20:debt<=50?15:debt<=60?10:debt<=70?5:0;
        
        let level='';
        if(score>=80)level='优秀';
        else if(score>=60)level='良好';
        else if(score>=40)level='一般';
        else level='较差';
        
        stockFinanceHealth.value={
            score:Math.round(score),
            level,
            roe:roe.toFixed(1),
            gross:gross.toFixed(1),
            debt:debt.toFixed(1)
        };
    }
    
    // 渲染营收趋势图
    function renderStockIncomeChart(incomeData){
        const el=document.getElementById('stockIncomeChart');
        if(!el||!incomeData?.length)return;
        if(window._stockIncomeChart)window._stockIncomeChart.dispose();
        const chart=echarts.init(el);
        window._stockIncomeChart=chart;
        
        const dates=incomeData.map(d=>d.end_date).reverse();
        const revenues=incomeData.map(d=>d.total_revenue/100000000).reverse(); // 转为亿
        const profits=incomeData.map(d=>d.net_profit/100000000).reverse();
        
        chart.setOption({
            tooltip:{trigger:'axis',axisPointer:{type:'cross'}},
            legend:{data:['营业收入','净利润'],textStyle:{color:'var(--text-secondary)'},top:0},
            grid:{left:'3%',right:'4%',bottom:'3%',containLabel:true},
            xAxis:{type:'category',data:dates,axisLine:{lineStyle:{color:'var(--border)'}},axisLabel:{color:'var(--text-muted)'}},
            yAxis:{type:'value',name:'亿元',axisLine:{lineStyle:{color:'var(--border)'}},axisLabel:{color:'var(--text-muted)'},splitLine:{lineStyle:{color:'var(--border)'}}},
            series:[
                {name:'营业收入',type:'bar',data:revenues,itemStyle:{color:'#3b82f6'}},
                {name:'净利润',type:'line',data:profits,itemStyle:{color:'#ef4444'},smooth:true}
            ]
        });
    }
    
    // 监听营收趋势tab切换
    watch(stockFinanceSubTab,(newVal)=>{
        if(newVal==='income'&&stockFinanceData.value?.income_trend?.length){
            nextTick(()=>renderStockIncomeChart(stockFinanceData.value.income_trend));
        }
    });

    function renderMarketStockKline(kd){
        const el=document.getElementById('marketStockKline');
        if(!el||!kd||!kd.dates?.length)return;
        if(window._mktKlineChart)window._mktKlineChart.dispose();
        const chart=echarts.init(el);
        window._mktKlineChart=chart;
        const upColor='#ef4444',downColor='#22c55e';
        chart.setOption({
            tooltip:{trigger:'axis',axisPointer:{type:'cross'}},
            legend:{data:['MA5','MA20'],textStyle:{color:'#9ca3af',fontSize:11},top:0},
            grid:[{left:55,right:10,top:35,height:'55%'},{left:55,right:10,top:'72%',height:'20%'}],
            xAxis:[{type:'category',data:kd.dates,gridIndex:0,axisLabel:{color:'#9ca3af',fontSize:9},boundaryGap:true},
                   {type:'category',data:kd.dates,gridIndex:1,axisLabel:{show:false},boundaryGap:true}],
            yAxis:[{type:'value',gridIndex:0,axisLabel:{color:'#9ca3af',fontSize:9},splitLine:{lineStyle:{color:'#1f2937'}}},
                   {type:'value',gridIndex:1,axisLabel:{color:'#9ca3af',fontSize:9,formatter:v=>v>=10000?(v/10000).toFixed(0)+'万':v.toFixed(0)},splitLine:{show:false}}],
            series:[
                {name:'K线',type:'candlestick',xAxisIndex:0,yAxisIndex:0,
                    data:kd.klines.map(k=>[k[0],k[1],k[2],k[3]]),
                    itemStyle:{color:upColor,color0:downColor,borderColor:upColor,borderColor0:downColor}},
                {name:'MA5',type:'line',xAxisIndex:0,yAxisIndex:0,data:kd.ma5,lineStyle:{color:'#f59e0b',width:1},symbol:'none'},
                {name:'MA20',type:'line',xAxisIndex:0,yAxisIndex:0,data:kd.ma20,lineStyle:{color:'#3b82f6',width:1},symbol:'none'},
                {name:'成交量',type:'bar',xAxisIndex:1,yAxisIndex:1,data:(kd.volumes||[]).map((v,i)=>{
                    const k=kd.klines[i];
                    return {value:v,itemStyle:{color:(k&&k[1]>=k[0])?upColor:downColor}};
                })},
            ]
        });
    }

    async function loadChips(){
        if(!marketStockDetail.value)return;
        chipsLoading.value=true;
        try{
            const r=await fetchWithAuth(`${API}/api/stock/${marketStockDetail.value.ts_code}/chips`);
            chipsData.value=await r.json();
            nextTick(()=>renderChipsChart());
        }catch(e){chipsData.value=null}
        finally{chipsLoading.value=false}
    }

    function renderChipsChart(){
        const el=document.getElementById('chipsChart');
        if(!el||!chipsData.value?.data?.length)return;
        if(window._chipsChart)window._chipsChart.dispose();
        const chart=echarts.init(el);
        window._chipsChart=chart;
        const data=chipsData.value.data;
        chart.setOption({
            tooltip:{trigger:'axis',formatter:p=>`价格: ${p[0]?.name}<br/>比例: ${p[0]?.value}%`},
            grid:{left:50,right:15,top:15,bottom:25},
            xAxis:{type:'category',data:data.map(d=>d.price?.toFixed(2)||''),axisLabel:{color:'#9ca3af',fontSize:10}},
            yAxis:{type:'value',axisLabel:{color:'#9ca3af',fontSize:10,formatter:'{value}%'}},
            series:[{
                type:'bar',
                data:data.map(d=>({
                    value:parseFloat(d.percent||0),
                    itemStyle:{color:d.change>0?'#ef4444':'#22c55e'}
                })),
                barMaxWidth:20
            }]
        });
    }

    async function loadStockNorthbound(){
        if(!marketStockDetail.value)return;
        stockNorthboundLoading.value=true;
        try{
            const r=await fetchWithAuth(`${API}/api/stock/${marketStockDetail.value.ts_code}/northbound-top10?days=30`);
            stockNorthboundData.value=await r.json();
        }catch(e){stockNorthboundData.value=null}
        finally{stockNorthboundLoading.value=false}
    }

    // 格式化金额（万元→亿）
    function formatAmount(v){
        if(v==null||isNaN(v))return'-';
        const n=parseFloat(v);
        if(Math.abs(n)>=100000)return(n/100000).toFixed(2)+'亿';
        if(Math.abs(n)>=10000)return(n/10000).toFixed(0)+'万';
        return n.toFixed(0);
    }

    let compareNormChart=null,comparePriceChart=null;

    function switchToCompare(){activeTab.value='compare'}

    let compareSearchTimer=null;
    async function searchCompareStock(){
        clearTimeout(compareSearchTimer);const kw=compareInput.value.trim();
        if(kw.length<1){compareSearchResults.value=[];return}
        compareSearchTimer=setTimeout(async()=>{try{const r=await fetchWithAuth(`${API}/api/search?keyword=${encodeURIComponent(kw)}`);const d=await r.json();compareSearchResults.value=d.results||[]}catch(e){compareSearchResults.value=[]}},300)}
    // 用watch监听输入变化触发搜索
    watch(compareInput,()=>{searchCompareStock()});

    function selectCompareStock(s){
        if(compareCodes.value.length>=4){showToast('最多4只');return}
        if(!compareCodes.value.includes(s.ts_code)){compareCodes.value.push(s.ts_code)}
        compareInput.value='';compareSearchResults.value=[];
    }
    function addCompareCode(){
        const code=compareInput.value.trim();if(!code)return;
        // [UI-04 修复] 正确映射股票代码：6/9开头 → SH，0/3开头 → SZ，8/4开头 → BJ
        let fullCode=code.toUpperCase();
        if(!fullCode.includes('.')){
            if(code.length===6){
                if(code.startsWith('6')||code.startsWith('9'))fullCode=code+'.SH';
                else if(code.startsWith('8')||code.startsWith('4'))fullCode=code+'.BJ';
                else fullCode=code+'.SZ';
            }else{fullCode=code}
        }
        if(!compareCodes.value.includes(fullCode)){
            if(compareCodes.value.length>=4){showToast('最多4只');return}
            compareCodes.value.push(fullCode);
        }
        compareInput.value='';compareSearchResults.value=[];
    }
    function removeCompareCode(i){compareCodes.value.splice(i,1)}

    async function runCompare(){
        if(compareCodes.value.length<2){showToast('请至少选择2只股票');return}
        compareLoading.value=true;compareData.value=null;
        try{
            const codes=compareCodes.value.join(',');
            const r=await fetchWithAuth(`${API}/api/compare?codes=${codes}&days=${compareDays.value}`);
            const d=await r.json();
            if(d.error){showToast(d.error);return}
            compareData.value=d;
            nextTick(()=>{renderCompareCharts()});
        }catch(e){showToast('对比请求失败')}finally{compareLoading.value=false}
    }

    function renderCompareCharts(){
        if(!compareData.value)return;
        const dates=compareData.value.dates||[];
        const stocks=compareData.value.stocks||[];

        // 标准化走势图
        const elNorm=document.getElementById('compareNormChart');
        if(elNorm){
            if(compareNormChart)compareNormChart.dispose();
            compareNormChart=echarts.init(elNorm,'dark');
            const series=stocks.map(s=>({
                name:s.name,type:'line',smooth:true,symbol:'none',
                lineStyle:{width:2,color:s.color},connectNulls:true,
                data:s.norm_closes||[]
            }));
            compareNormChart.setOption({
                backgroundColor:'transparent',
                tooltip:{trigger:'axis',valueFormatter:v=>v!=null?v.toFixed(2):'-'},
                legend:{data:stocks.map(s=>s.name),top:0,textStyle:{color:'#8b8fa3',fontSize:11}},
                grid:{left:60,right:20,top:40,bottom:30},
                xAxis:{type:'category',data:dates,axisLabel:{color:'#8b8fa3',fontSize:10},axisLine:{lineStyle:{color:'#2a2e3f'}},splitLine:{show:false}},
                yAxis:{type:'value',name:'基准100',nameTextStyle:{color:'#8b8fa3',fontSize:10},axisLabel:{color:'#8b8fa3',fontSize:10},splitLine:{lineStyle:{color:'#1e2130'}},axisLine:{show:false}},
                series,
                dataZoom:[{type:'inside',start:0,end:100},{type:'slider',start:0,end:100,bottom:2,height:16,textStyle:{color:'#8b8fa3',fontSize:10},borderColor:'#2a2e3f',fillerColor:'rgba(59,130,246,0.15)'}]
            });
        }

        // 绝对价格走势图
        const elPrice=document.getElementById('comparePriceChart');
        if(elPrice){
            if(comparePriceChart)comparePriceChart.dispose();
            comparePriceChart=echarts.init(elPrice,'dark');
            const series=stocks.map(s=>({
                name:s.name,type:'line',smooth:true,symbol:'none',
                lineStyle:{width:1.5,color:s.color},connectNulls:true,
                data:s.closes||[]
            }));
            comparePriceChart.setOption({
                backgroundColor:'transparent',
                tooltip:{trigger:'axis',valueFormatter:v=>v!=null?'¥'+v.toFixed(2):'-'},
                legend:{data:stocks.map(s=>s.name),top:0,textStyle:{color:'#8b8fa3',fontSize:11}},
                grid:{left:60,right:20,top:40,bottom:30},
                xAxis:{type:'category',data:dates,axisLabel:{color:'#8b8fa3',fontSize:10},axisLine:{lineStyle:{color:'#2a2e3f'}},splitLine:{show:false}},
                yAxis:{type:'value',axisLabel:{color:'#8b8fa3',fontSize:10,formatter:v=>'¥'+v.toFixed(2)},splitLine:{lineStyle:{color:'#1e2130'}},axisLine:{show:false}},
                series,
                dataZoom:[{type:'inside',start:0,end:100},{type:'slider',start:0,end:100,bottom:2,height:16,textStyle:{color:'#8b8fa3',fontSize:10},borderColor:'#2a2e3f',fillerColor:'rgba(59,130,246,0.15)'}]
            });
        }
    }
    async function handleImport(){importError.value='';try{const data=JSON.parse(importData.value);data.import_mode=importMode.value;const r=await fetchWithAuth(`${API}/api/import`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const d=await r.json();if(r.ok){showImportModal.value=false;importData.value='';showToast(d.message||'导入成功');await fetchData()}else{importError.value=d.error||'导入失败'}}catch(e){importError.value='JSON 格式无效'}}

    // Filter & Sort
    const filteredPositions=computed(()=>{let list=[...positions.value];if(searchKeyword.value.trim()){const kw=searchKeyword.value.trim().toLowerCase();list=list.filter(p=>p.name.toLowerCase().includes(kw)||p.ts_code.toLowerCase().includes(kw)||(p.industry&&p.industry.includes(kw)))}list.sort((a,b)=>{let va=a[sortKey.value],vb=b[sortKey.value];if(typeof va==='string'){va=va.toLowerCase();vb=(vb||'').toLowerCase()}if(va<vb)return sortDir.value==='asc'?-1:1;if(va>vb)return sortDir.value==='asc'?1:-1;return 0});return list});
    function filterPositions(){}
    function sortPositions(){}

    // Charts
    let pieChart=null,barChart=null,emotionChart=null,winRateChart=null,profitChart=null;
    function renderCharts(){renderPieChart();renderBarChart()}
    function renderPieChart(){const el=document.getElementById('pieChart');if(!el)return;if(!pieChart)pieChart=echarts.init(el,'dark');const stockData=positions.value.filter(p=>p.market_value>0).map(p=>({name:p.name,value:p.market_value}));const cash=capitalForm.value.cash||0;if(cash>0){stockData.push({name:'💰 现金',value:cash,itemStyle:{color:'#3b82f6'}})}pieChart.setOption({backgroundColor:'transparent',tooltip:{trigger:'item',formatter:p=>`${p.marker}${p.name}: ¥${Number(p.value).toLocaleString()} (${p.percent}%)`},series:[{type:'pie',radius:['40%','70%'],avoidLabelOverlap:true,itemStyle:{borderRadius:6,borderColor:'#1e2130',borderWidth:2},label:{color:'#8b8fa3',fontSize:12,formatter:'{b}\n{d}%'},emphasis:{label:{fontSize:14,fontWeight:'bold',color:'#e4e6f0'},itemStyle:{shadowBlur:10,shadowColor:'rgba(0,0,0,0.3)'}},data:stockData}]})}
    function renderBarChart(){const el=document.getElementById('barChart');if(!el)return;if(!barChart)barChart=echarts.init(el,'dark');const names=positions.value.map(p=>p.name),profits=positions.value.map(p=>p.profit);barChart.setOption({backgroundColor:'transparent',tooltip:{trigger:'axis',formatter:params=>{const d=params[0];return`${d.name}<br/>盈亏: <span style="color:${d.value>=0?'#ef4444':'#22c55e'}">¥${Math.abs(d.value).toLocaleString()}</span>`}},grid:{left:60,right:20,top:20,bottom:40},xAxis:{type:'category',data:names,axisLabel:{color:'#8b8fa3',fontSize:11,rotate:names.length>6?30:0},axisLine:{lineStyle:{color:'#2a2e3f'}}},yAxis:{type:'value',axisLabel:{color:'#8b8fa3',fontSize:11,formatter:v=>(v>=0?'+':'')+v},splitLine:{lineStyle:{color:'#1e2130'}},axisLine:{show:false}},series:[{type:'bar',data:profits.map(v=>({value:v,itemStyle:{color:v>=0?'#ef4444':'#22c55e',borderRadius:[4,4,0,0]}})),barMaxWidth:40}]})}

    function renderAnalysisCharts(){
        // Emotion pie
        const elEmo=document.getElementById('emotionChart');if(!elEmo)return;
        if(!emotionChart)emotionChart=echarts.init(elEmo,'dark');
        const emoCount={};tradeLogs.value.forEach(t=>{if(t.emotion){const label=EL[t.emotion]||t.emotion;emoCount[label]=(emoCount[label]||0)+1}});
        const emoData=Object.entries(emoCount).map(([name,value])=>({name,value})).sort((a,b)=>b.value-a.value);
        emotionChart.setOption({backgroundColor:'transparent',title:{text:emoData.length===0?'暂无数据':'',left:'center',top:'center',textStyle:{color:'#565a6e',fontSize:14}},tooltip:{trigger:'item',formatter:'{b}: {c}笔 ({d}%)'},series:[{type:'pie',radius:['40%','70%'],itemStyle:{borderRadius:6,borderColor:'#1e2130',borderWidth:2},label:{color:'#8b8fa3',fontSize:11},data:emoData.length>0?emoData:[{name:'暂无数据',value:1,itemStyle:{color:'#262a3a'}}]}]});

        // Win rate gauge
        const elWin=document.getElementById('winRateChart');if(!elWin)return;
        if(!winRateChart)winRateChart=echarts.init(elWin,'dark');
        const wr=tradeLogStats.value?.win_rate||0;
        winRateChart.setOption({backgroundColor:'transparent',series:[{type:'gauge',startAngle:200,endAngle:-20,min:0,max:100,splitNumber:10,itemStyle:{color:wr>=50?'#ef4444':'#22c55e'},progress:{show:true,width:18},pointer:{show:false},axisLine:{lineStyle:{width:18,color:[[1,'#262a3a']]}},axisTick:{show:false},splitLine:{show:false},axisLabel:{color:'#8b8fa3',fontSize:11,distance:15,formatter:v=>v+'%'},title:{show:true,offsetCenter:[0,'70%'],fontSize:14,color:'#8b8fa3'},detail:{valueAnimation:true,fontSize:28,fontWeight:'bold',offsetCenter:[0,'40%'],formatter:`{value}%`,color:wr>=50?'#ef4444':'#22c55e'},data:[{value:wr,name:'卖出胜率'}]}]});

        // Profit trend bar
        const elProfit=document.getElementById('profitChart');if(!elProfit)return;
        if(!profitChart)profitChart=echarts.init(elProfit,'dark');
        const sells=tradeLogs.value.filter(t=>t.trade_type==='sell').sort((a,b)=>a.date.localeCompare(b.date));
        const dates=sells.map(t=>t.date),profits=sells.map(t=>t.profit);
        profitChart.setOption({backgroundColor:'transparent',tooltip:{trigger:'axis',formatter:params=>{const d=params[0];return`${d.name}<br/>盈亏: <span style="color:${d.value>=0?'#ef4444':'#22c55e'}">¥${Math.abs(d.value).toLocaleString()}</span>`}},grid:{left:60,right:20,top:20,bottom:40},xAxis:{type:'category',data:dates,axisLabel:{color:'#8b8fa3',fontSize:11,rotate:dates.length>8?30:0},axisLine:{lineStyle:{color:'#2a2e3f'}}},yAxis:{type:'value',axisLabel:{color:'#8b8fa3',fontSize:11},splitLine:{lineStyle:{color:'#1e2130'}},axisLine:{show:false}},series:[{type:'bar',data:profits.map(v=>({value:v,itemStyle:{color:v>=0?'#ef4444':'#22c55e',borderRadius:[4,4,0,0]}})),barMaxWidth:40}]});
    }

    let refreshTimer=null;
    function startAutoRefresh(){refreshTimer=setInterval(()=>{if(isLoggedIn.value){fetchData();fetchIndex()}},8000)}
    function handleResize(){pieChart&&pieChart.resize();barChart&&barChart.resize();emotionChart&&emotionChart.resize();winRateChart&&winRateChart.resize();profitChart&&profitChart.resize();klineChart&&klineChart.resize();compareNormChart&&compareNormChart.resize();comparePriceChart&&comparePriceChart.resize();spWinRateChart&&spWinRateChart.resize();spAvgChgChart&&spAvgChgChart.resize()}

    onMounted(()=>{if(isLoggedIn.value){fetchData();fetchIndex();fetchCapital()}startTokenRefresh();startAutoRefresh();window.addEventListener('resize',handleResize)});
    onUnmounted(()=>{clearInterval(refreshTimer);clearInterval(screenPollTimer);if(alertAutoTimer.value)clearInterval(alertAutoTimer.value);window.removeEventListener('resize',handleResize);pieChart&&pieChart.dispose();barChart&&barChart.dispose();emotionChart&&emotionChart.dispose();winRateChart&&winRateChart.dispose();profitChart&&profitChart.dispose();klineChart&&klineChart.dispose();compareNormChart&&compareNormChart.dispose();comparePriceChart&&comparePriceChart.dispose();spWinRateChart&&spWinRateChart.dispose();spAvgChgChart&&spAvgChgChart.dispose()});

    return{isLoggedIn,currentUser,showAuthModal,authMode,authError,authSubmitting,loginForm,registerForm,doLogin,doRegister,doLogout,
        positions,summary,loading,searchKeyword,sortKey,sortDir,activeTab,indexData,indexSource,indexTime,tradeLogs,tradeLogStats,capitalForm,
        screenMarket,screenStats,screenResults,screenHistory,screenInfo,screenBonusTags,
        currentStrategy,strategyList,currentStrategyMeta,screenStrategyReason,
        showAddModal,showSellModal,showDetailModal,showAlertModal,showImportModal,showParamsModal,showAdviceModal,editingPosition,detailPosition,sellTarget,alertTarget,adviceTarget,
        emotionLabels,addForm,sellForm,alertForm,addError,sellError,submitting,submittingSell,searchResults,importData,importMode,importError,
        toastMsg,sellPreview,filteredPositions,priceLevels,priceLevelsLoading,priceLevelsError,adviceData,adviceLoading,adviceError,
        reviewPeriod,reviewLoading,reviewData,regimeLoading,regimeData,backtestDays,backtestHold,backtestLoading,backtestData,alertCheckLoading,alertCheckResult,alertAutoMode,alertAutoTimer,toggleAlertAuto,
        klineData,klineLoading,
        screenParams,paramsLoading,paramsDefault,forceMode,
        refreshData,searchStock,selectStock,submitPosition,openAddModal,openTradeModal,
        openSellModal,submitSell,openAlertModal,submitAlerts,clearAlerts,openDetailModal,closeDetailModal,openAdviceModal,quickAddFromAdvice,quickSellFromAdvice,confirmDelete,deleteTrade,
        saveCapital,switchToTradeLog,switchToAnalysis,switchToScreener,switchToReview,switchToStrategy,
        loadReview,loadRegime,runBacktest,checkAlerts,loadKline,
        startScreen,startScreenForce,pollScreenResult,loadScreenResult,quickBuyFromScreen,
        addToWatch,addAllToWatch,showWatchReport,watchAdding,watchList,watchReport,
        watchItems,watchLoading,watchListCount,watchSearch,watchFilter,watchReportData,
        watchAddModal,watchAddForm,watchAddSearch,watchTagModal,watchTagTarget,watchTagForm,
        watchNoteModal,watchNoteTarget,watchNoteForm,
        showWatchAdviceModal,watchAdviceTarget,watchAdviceData,watchAdviceLoading,watchAdviceError,
        showWatchStrategyModal,watchStrategyTarget,watchStrategyData,watchStrategyLoading,watchStrategyError,
        watchStats,watchGroups,filteredWatchItems,
        switchToWatch,loadWatchList,removeWatchItem,clearWatchList,loadWatchReport,
        searchWatchStock,selectWatchStock,submitWatchAdd,quickBuyFromWatch,
        openWatchTagModal,submitWatchTag,openWatchNoteModal,submitWatchNote,
        openAdviceModalForWatch,openWatchStrategyModal,openKlineModalForWatch,translateStrategy,
        resetParams,onParamChange,confirmRunScreen,
        handleExport,handleImport,filterPositions,sortPositions,formatNum,round2,
        compareCodes,compareInput,compareDays,compareData,compareLoading,compareSearchResults,compareColors,
        switchToCompare,selectCompareStock,addCompareCode,removeCompareCode,runCompare,
        spLoading,spData,loadStrategyPerformance,
        // P1+P2 市场数据
        marketSubTab,switchToMarket,
        limitListData,limitListLoading,loadLimitList,
        limitStepData,limitStepLoading,limitCptData,limitCptLoading,
        northboundData,northboundLoading,loadNorthbound,renderNorthboundChart,getNorthboundColor,
        topListData,topListLoading,loadTopList,
        sectorFlowData,sectorFlowLoading,loadSectorFlow,
        sectorRotationData,sectorRotationLoading,loadSectorRotation,
        showMarketStockModal,marketStockDetail,openMarketStockDetail,
        stockDetailSubTab,stockFinanceData,stockFinanceLoading,
        chipsData,chipsLoading,loadChips,renderChipsChart,
        stockNorthboundData,stockNorthboundLoading,loadStockNorthbound,
        // 个股财务详情弹窗
        showStockFinanceModal,stockFinanceModalTitle,stockFinanceModalCode,stockFinanceSubTab,stockFinanceHealth,
        openStockFinanceModal,calculateFinanceHealth,renderStockIncomeChart,
        formatAmount};
}
}).mount('#app');
