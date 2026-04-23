const{ref,computed,watch,onMounted,onUnmounted,nextTick,h}=Vue;
const API='';
const EL={calm:'😌 冷静',confident:'😎 自信',fomo:'😰 怕错过',greedy:'🤑 贪婪',panic:'😱 恐慌',revenge:'😡 报复性',hesitant:'🤔 犹豫',impulsive:'⚡ 冲动'};

// ============================================================
// 量价状态辅助工具 (v3.4.8 交互体验优化)
// ============================================================
const volumePriceHelper = {
    // 根据信号获取颜色（支持新旧信号系统）
    getColor(signal) {
        const map = {
            // 旧信号系统
            'volume_down': '#ef4444',    // 红色 - 放量下跌危险
            'weak_down': '#f59e0b',      // 橙色 - 缩量下跌警告
            'volume_up': '#10b981',      // 绿色 - 放量上涨健康
            'low_vol': '#6b7280',        // 灰色 - 缩量横盘中性
            'normal': '#3b82f6',         // 蓝色 - 正常状态
            // 新信号系统（v3.4.10+）
            'danger': '#ef4444',         // 红色 - 危险/卖出
            'strong': '#22c55e',         // 绿色 - 强势/买入
            'good': '#3b82f6',           // 蓝色 - 良好/持有
            'weak': '#94a3b8',           // 灰色 - 弱势/观望
            'normal': '#6b7280'          // 中灰 - 正常
        };
        return map[signal] || '#6b7280';
    },
    
    // 根据信号获取图标（支持新旧信号系统）
    getIcon(signal) {
        const map = {
            // 旧信号系统
            'volume_down': '📉',
            'weak_down': '⚠️',
            'volume_up': '📈',
            'low_vol': '↔️',
            'normal': '✅',
            // 新信号系统（v3.4.10+）
            'danger': '📉',
            'strong': '🔥',
            'good': '📈',
            'weak': '⚪',
            'normal': '📊'
        };
        return map[signal] || '📊';
    },
    
    // 根据信号和数值生成详细描述（支持新旧信号系统）
    getDescription(signal, vol_ratio, pct_chg) {
        const descMap = {
            // 旧信号系统
            'volume_down': `放量下跌（量比${vol_ratio}），主力出货信号`,
            'weak_down': `缩量下跌（量比${vol_ratio}），抛压减轻`,
            'volume_up': `放量上涨（量比${vol_ratio}），动能增强`,
            'low_vol': `成交清淡（量比${vol_ratio}），横盘整理`,
            'normal': `成交量正常（量比${vol_ratio}）`,
            // 新信号系统（v3.4.10+） - 与后端返回的short_desc对应
            'danger': `放量下跌（量比${vol_ratio}），警惕出货`,
            'strong': `成交极度活跃（量比${vol_ratio}），量价齐升`,
            'good': `成交活跃（量比${vol_ratio}），上涨有量支撑`,
            'weak': vol_ratio < 0.8 ? `成交清淡（量比${vol_ratio}），缺乏动能` : `成交量正常但上涨量能不足（量比${vol_ratio}）`,
            'normal': `成交量正常（量比${vol_ratio}），上涨量能充足`
        };
        return descMap[signal] || `量比${vol_ratio}`;
    },
    
    // 生成悬停提示文本
    getTooltip(signal, vol_ratio, pct_chg, up_vol_avg, down_vol_avg) {
        const base = `量比: ${vol_ratio}（比5日均量高${((vol_ratio-1)*100).toFixed(0)}%）`;
        let trend = '';
        if (up_vol_avg && down_vol_avg) {
            const ratio = (up_vol_avg / down_vol_avg).toFixed(1);
            trend = `上涨日均量是下跌日的${ratio}倍`;
        }
        
        const adviceMap = {
            // 旧信号系统
            'volume_down': '强烈建议减仓或止损',
            'weak_down': '关注支撑位，可持有观察',
            'volume_up': '趋势健康，可继续持有',
            'low_vol': '交投清淡，建议观望',
            'normal': '正常波动，无需特别操作',
            // 新信号系统（v3.4.10+）
            'danger': '放量下跌，警惕出货，建议减仓或止损',
            'strong': '成交极度活跃，量价齐升，可继续持有或加仓',
            'good': '成交活跃，上涨有量支撑，可继续持有',
            'weak': '交投清淡或上涨量能不足，建议观望等待放量信号',
            'normal': '成交量正常，上涨有量支撑，维持当前仓位'
        };
        
        return [base, trend, adviceMap[signal]].filter(Boolean).join(' | ');
    }
};

// ============================================================
// 交互体验增强工具集 (v3.4.9 量价指标描述优化)
// ============================================================
const interactionHelper = {
    // 颜色分类系统
    getSignalColor(signal) {
        const colorMap = {
            'danger': '#ef4444',      // 红色 - 危险/卖出
            'strong': '#22c55e',      // 绿色 - 强势/买入
            'good': '#3b82f6',        // 蓝色 - 良好/持有
            'weak': '#94a3b8',        // 灰色 - 弱势/观望
            'normal': '#6b7280'       // 中灰 - 正常
        };
        return colorMap[signal] || '#6b7280';
    },
    
    // 图标映射系统
    getSignalIcon(signal) {
        const iconMap = {
            'danger': '📉',
            'strong': '🔥',
            'good': '📈',
            'weak': '⚪',
            'normal': '📊'
        };
        return iconMap[signal] || '📊';
    },
    
    // 自然语言描述生成器
    generateVolumeDescription(volumeData) {
        const { signal, vol_ratio, pct_chg, up_vol_avg, down_vol_avg } = volumeData;
        
        // 第一层：简短描述
        const shortDescMap = {
            'danger': '放量下跌，警惕出货',
            'strong': '成交极度活跃，量价齐升',
            'good': '成交活跃，上涨有量支撑',
            'weak': (vol_ratio < 0.8) ? '成交清淡，缺乏动能' : '成交量正常但上涨量能不足',
            'normal': '成交量正常，上涨量能充足'
        };
        
        // 第二层：详细解释
        let explanation = '';
        if (vol_ratio > 1.5) {
            explanation = `成交量非常活跃（比平时高${((vol_ratio-1)*100).toFixed(0)}%）`;
        } else if (vol_ratio > 1.2) {
            explanation = `成交量活跃（比平时高${((vol_ratio-1)*100).toFixed(0)}%）`;
        } else if (vol_ratio < 0.8) {
            explanation = `成交清淡（比平时低${((1-vol_ratio)*100).toFixed(0)}%）`;
        } else {
            explanation = '成交量在正常范围内';
        }
        
        // 第三层：资金流向分析
        let flowAnalysis = '';
        if (up_vol_avg && down_vol_avg) {
            const ratio = (up_vol_avg / down_vol_avg).toFixed(1);
            if (ratio > 1.5) {
                flowAnalysis = `上涨日成交量是下跌日的${ratio}倍，显示资金积极流入`;
            } else if (ratio > 1.0) {
                flowAnalysis = `上涨日成交量略高于下跌日，资金面偏积极`;
            } else {
                flowAnalysis = `下跌日成交量较高，显示抛压相对较大`;
            }
        }
        
        return {
            short: shortDescMap[signal] || '成交量分析',
            explanation: explanation,
            flow: flowAnalysis,
            full: [shortDescMap[signal], explanation, flowAnalysis].filter(Boolean).join('。') + '。'
        };
    },
    
    // 操作建议生成器
    generateAdvice(signal, profitPct = 0) {
        const adviceMap = {
            'danger': {
                title: '建议减仓',
                detail: '放量下跌是主力出货信号，建议及时减仓控制风险',
                urgency: 'high',
                actions: ['减仓50%', '设置严格止损', '避免加仓']
            },
            'strong': {
                title: '可加仓',
                detail: '量价配合良好，上涨有量支撑，可考虑加仓',
                urgency: 'medium',
                actions: ['可加仓20-30%', '设置移动止盈', '持有为主']
            },
            'good': {
                title: '继续持有',
                detail: '量价关系健康，趋势稳定，继续持有',
                urgency: 'low',
                actions: ['继续持有', '关注阻力位', '跌破支撑位考虑减仓']
            },
            'weak': {
                title: '观望',
                detail: (vol_ratio < 0.8) ? '成交清淡，缺乏动能，建议观望等待放量信号' : '成交量正常但上涨量能不足，建议观望等待放量确认',
                urgency: 'low',
                actions: ['观望', '等待放量信号', '不追高']
            },
            'normal': {
                title: '维持仓位',
                detail: '成交量正常，上涨量能充足，维持当前仓位',
                urgency: 'none',
                actions: ['维持仓位', '关注技术面变化', '按计划操作']
            }
        };
        
        const baseAdvice = adviceMap[signal] || adviceMap.normal;
        
        // 结合盈亏状态调整建议
        if (profitPct > 20 && signal === 'strong') {
            baseAdvice.detail += ' 当前盈利丰厚，可考虑分批止盈锁定利润';
            baseAdvice.actions.push('分批止盈');
        }
        
        return baseAdvice;
    },
    
    // 生成可视化CSS类
    getVisualClass(signal) {
        const classMap = {
            'danger': 'vp-danger',
            'strong': 'vp-strong',
            'good': 'vp-good',
            'weak': 'vp-weak',
            'normal': 'vp-normal'
        };
        return classMap[signal] || 'vp-normal';
    },
    
    // 生成迷你进度条HTML（用于量比可视化）
    generateMiniBar(vol_ratio, max = 2.0) {
        const percentage = Math.min(100, (vol_ratio / max) * 100);
        let color = '#10b981'; // 绿色
        if (vol_ratio > 1.5) color = '#22c55e'; // 深绿
        else if (vol_ratio < 0.8) color = '#94a3b8'; // 灰色
        
        return `
            <div class="mini-bar-container" style="width: 60px; height: 8px; background: #e5e7eb; border-radius: 4px; overflow: hidden;">
                <div class="mini-bar-fill" style="width: ${percentage}%; height: 100%; background: ${color}; border-radius: 4px;"></div>
            </div>
        `;
    }
};

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

Vue.createApp({
setup(){
    const positions=ref([]),summary=ref({total_market_value:0,total_cost:0,total_profit:0,total_profit_pct:0,today_profit:0,position_count:0,is_trade_time:false,last_update:'-',cash:0,initial_capital:0,total_assets:0,alert_count:0});
    const loading=ref(false),searchKeyword=ref(''),sortKey=ref('market_value'),sortDir=ref('desc'),activeTab=ref('positions');
    const indexData=ref({}),tradeLogs=ref([]),tradeLogStats=ref(null),capitalForm=ref({initial:0,cash:0});
    const screenMarket=ref(null),screenStats=ref(null),screenResults=ref([]),screenHistory=ref([]);
    const screenInfo=ref({running:false,hasResult:false,lastRun:null,runTime:null});
    const watchAdding=ref(false),watchList=ref([]),watchReport=ref(null);
    const currentStrategy=ref('trend_break');
    // 多策略结果缓存（keyed by strategy name）
    const allScreenResults=ref({});
    // 各策略执行摘要（供策略卡片展示）
    const strategySummaries=ref({});
    // 正在运行的策略列表
    const runningStrategies=ref([]);
    // 策略详情展开状态（keyed by strategy name，默认全部收起）
    const expandedDetails=ref({});
    function toggleDetails(key){expandedDetails.value[key]=!expandedDetails.value[key]}
    const DEFAULT_STRATEGIES = {
        'trend_break': {
            name: '趋势突破', icon: '📈', suitable: '大盘上升/震荡期',
            description: '买入信号：①趋势面(~37分)MA20位置(5)+方向(4)+HL结构(4)+MACD状态(0~12)+均线多头(0~10)；②板块(15分)相对超额收益+概念热度排名；③资金(10分)主力净流入+大单结构+持续性；④共振(15分)三看共振(全过15/二看9/一看5)；⑤催化(10分)业绩预告/回购/涨停/缩量回踩'
        },
        'sector_leader': { name: '板块龙头', icon: '🏆', suitable: '短期热点追踪' },
        'oversold_bounce': { name: '超跌反弹', icon: '📉', suitable: '抄底机会捕捉',
            description: '①趋势(35分)跌幅分级+止跌信号(长下影/放量阳/MACD金叉)；②板块(15/5)热门概念匹配；③资金(15分)净流入+连续天数+占比；④加分(15)市值区间+业绩预告+回购；⑤技术改善(15)MA金叉+显著放量+阳线；⑥HL结构(-5~10)' }
    };
    const strategyList=ref({...DEFAULT_STRATEGIES});
    // 使用普通对象作为安全访问层，避免 computed 在初始化时的 undefined 问题
    const strategyListSafe = {...DEFAULT_STRATEGIES};
    const currentStrategyMeta=computed(()=>strategyList.value[currentStrategy.value]||null);
    const screenStrategyReason=ref('');
    const screenStrategyRecommended=ref('');
    const strategyBacktestData=ref({});
    const showAddModal=ref(false),showSellModal=ref(false),showDetailModal=ref(false),showAlertModal=ref(false),showImportModal=ref(false),showParamsModal=ref(false),showAdviceModal=ref(false),showHeaderMenu=ref(false),showTradeDetailModal=ref(false),showCapitalModal=ref(false);
    const editingPosition=ref(null),detailPosition=ref(null),sellTarget=ref(null),alertTarget=ref(null),tradeDetailTarget=ref(null),adviceTarget=ref(null),adviceData=ref(null),adviceLoading=ref(false),adviceError=ref('');
    const threeViewsCheck=ref(null),threeViewsLoading=ref(false),threeViewsError=ref('');
    const sellCheckData=ref(null),sellCheckLoading=ref(false),sellConfirmedCheck=ref(false);
    const buyConfirmedCheck=ref(false);
    const emotionLabels=EL;
    const addForm=ref({ts_code:'',buy_price:'',buy_volume:'',buy_date:new Date().toISOString().slice(0,10),fee:0,note:'',reason:'',emotion:''});
    const sellForm=ref({sell_price:'',sell_volume:'',sell_date:new Date().toISOString().slice(0,10),fee:0,note:'',reason:''});
    const alertForm=ref({stop_loss:'',stop_profit:''});
    const priceLevels=ref(null),priceLevelsLoading=ref(false),priceLevelsError=ref('');
    const addError=ref(''),sellError=ref(''),submitting=ref(false),submittingSell=ref(false);
    const actionMenuOpen=ref(null);
    const searchResults=ref([]),importData=ref(''),importMode=ref('replace'),importError=ref('');
    const toastMsg=ref('');let toastTimer=null;

    // ============================================================
    // 选股结果展开行状态
    // ============================================================
    const expandedScreenRow=ref(null);
    function toggleScreenRow(ts_code){
        expandedScreenRow.value=expandedScreenRow.value===ts_code?null:ts_code;
    }

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
    const stockFinanceHealth=ref({score:0,level:'-',levelClass:'',roe:0,gross:0,debt:0,breakdown:{profit:0,efficiency:0,safety:0,growth:0}});
    
    // 财务指标计算属性
    const financeKpiClass=computed(()=>{
        const fina=stockFinanceData.value?.fina_indicator?.[0];
        const health=stockFinanceHealth.value;
        const pe=fina?.valuation?.pe_ttm||0;
        const roe=health.roe||0;
        const dv=fina?.valuation?.dv_ttm||0;
        const pb=fina?.valuation?.pb||0;
        const gross=health.gross||0;
        const debt=health.debt||0;
        return{
            pe:pe<20?'text-green':pe<40?'text-yellow':'text-red',
            roe:roe>=0.15?'text-red':roe>=0.10?'text-yellow':'text-green',
            dividend:dv>3?'text-red':dv>1?'text-yellow':'text-green',
            pb:pb<3?'text-green':pb<5?'text-yellow':'text-red',
            margin:gross>=0.30?'text-red':gross>=0.20?'text-yellow':'text-green',
            debt:debt<=0.4?'text-green':debt<=0.6?'text-yellow':'text-red'
        };
    });
    
    const financeKpiTag=computed(()=>{
        const fina=stockFinanceData.value?.fina_indicator?.[0];
        const health=stockFinanceHealth.value;
        const pe=fina?.valuation?.pe_ttm||0;
        const roe=health.roe||0;
        const dv=fina?.valuation?.dv_ttm||0;
        return{
            pe:pe<20?'低估':pe<40?'合理':'高估',
            roe:roe>=0.15?'优秀':roe>=0.10?'良好':'一般',
            dividend:dv>3?'高股息':dv>1?'适中':'较低'
        };
    });
    
    const financeKpiTagClass=computed(()=>{
        const fina=stockFinanceData.value?.fina_indicator?.[0];
        const health=stockFinanceHealth.value;
        const pe=fina?.valuation?.pe_ttm||0;
        const roe=health.roe||0;
        const dv=fina?.valuation?.dv_ttm||0;
        return{
            pe:pe<20?'positive':pe<40?'neutral':'negative',
            roe:roe>=0.15?'positive':roe>=0.10?'neutral':'negative',
            dividend:dv>3?'positive':dv>1?'neutral':'negative'
        };
    });

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

    // ============================================================
    // 适老化字体大小切换
    // ============================================================
    const FONT_SIZE_KEY='stock_font_size';
    const fontSize=ref(localStorage.getItem(FONT_SIZE_KEY)||'normal');
    function applyFontSize(size){
        const html=document.documentElement;
        html.classList.remove('font-large','font-xlarge');
        if(size==='large')html.classList.add('font-large');
        else if(size==='xlarge')html.classList.add('font-xlarge');
    }
    function setFontSize(size){
        fontSize.value=size;
        localStorage.setItem(FONT_SIZE_KEY,size);
        applyFontSize(size);
    }
    // 初始化时应用保存的字体大小
    applyFontSize(fontSize.value);

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
    const showStrategyDetail=ref(false); // 策略详情弹窗
    const backtestDays=ref(30),backtestHold=ref(5),backtestLoading=ref(false),backtestData=ref(null);
    const alertCheckLoading=ref(false),alertCheckResult=ref(null);
    const positionAdvice=ref(null);const showPositionAdviceDetail=ref(false);
    const alertAutoMode=ref(false),alertAutoTimer=ref(null);
    // K-line
    const klineData=ref(null),klineLoading=ref(false);
    const klineIndicator=ref('MACD'); // 副图指标: MACD/KDJ/RSI/BOLL/VOL
    const maVisibility=ref({ma5:true,ma10:false,ma20:true,ma60:false,ma120:false,ma250:false});
    // Params
    const screenParams=ref([]);
    const paramsLoading=ref(false);
    const paramsDefault=ref([]); // 保存默认值用于重置
    const forceMode=ref(false);
    // Strategy Performance
    const spLoading=ref(false),spData=ref(null),spError=ref('');
    const spLoadedAt=ref(null); // 缓存时间戳
    const SP_CACHE_TTL=5*60*1000; // 5分钟缓存
    let spWinRateChart=null,spAvgChgChart=null;

    function showToast(m){toastMsg.value=m;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{toastMsg.value=''},3000)}
    function round2(n){return Math.round(n*100)/100}
    function formatNum(n){if(n===null||n===undefined)return'-';return Number(n).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}
    function getAlertsTooltip(alerts){if(!alerts||alerts.length===0)return'';return alerts.map(a=>a.message+(a.detail?' | '+a.detail:'')).join('\n')}

    async function fetchData(){
        if(loading.value)return;loading.value=true;
        try{const r=await fetchWithAuth(`${API}/api/positions`);const d=await r.json();positions.value=d.positions||[];summary.value={...summary.value,...d.summary};nextTick(()=>renderCharts())}catch(e){console.error(e)}finally{loading.value=false}
    }
    async function fetchIndex(){try{const r=await fetchWithAuth(`${API}/api/index`);const d=await r.json();indexData.value=d;const codes=Object.keys(d);if(codes.length>0){const first=d[codes[0]];indexSource.value=first._source||'';if(first.time){const t=first.time;indexTime.value=t.includes(' ')?t.split(' ')[1]:t}else{indexTime.value=new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}}else{indexSource.value='';indexTime.value=''}}catch(e){}}
    async function fetchTradeLog(){try{const r=await fetchWithAuth(`${API}/api/trade-log`);const d=await r.json();tradeLogs.value=d.trades||[];tradeLogStats.value=d.stats||null}catch(e){}}
    async function fetchCapital(){try{const r=await fetchWithAuth(`${API}/api/capital`);const d=await r.json();capitalForm.value=d.capital||{initial:0,cash:0}}catch(e){}}
    async function loadPositionAdvice(){try{const r=await fetchWithAuth(`${API}/api/position-advice`);const d=await r.json();positionAdvice.value=d;}catch(e){positionAdvice.value=null;}}

    async function refreshData(){await Promise.all([fetchData(),fetchIndex()]);if(activeTab.value==='tradelog')await fetchTradeLog();await loadPositionAdvice();showToast('数据已刷新')}

    let searchTimer=null;
    async function searchStock(){clearTimeout(searchTimer);const kw=addForm.value.ts_code.trim();if(kw.length<1){searchResults.value=[];return}searchTimer=setTimeout(async()=>{try{const r=await fetchWithAuth(`${API}/api/search?keyword=${encodeURIComponent(kw)}`);const d=await r.json();searchResults.value=d.results||[]}catch(e){searchResults.value=[]}},300)}
    function selectStock(s){addForm.value.ts_code=s.ts_code;searchResults.value=[]}

    function resetForm(){addForm.value={ts_code:'',buy_price:'',buy_volume:'',buy_date:new Date().toISOString().slice(0,10),fee:0,note:'',reason:'',emotion:''};editingPosition.value=null;searchResults.value=[];buyConfirmedCheck.value=false;threeViewsCheck.value=null;threeViewsError.value=''}
    function openAddModal(){resetForm();showAddModal.value=true}
    function openTradeModal(p){editingPosition.value=p;addForm.value={ts_code:p.ts_code,buy_price:'',buy_volume:'',buy_date:new Date().toISOString().slice(0,10),fee:0,note:'',reason:'',emotion:''};buyConfirmedCheck.value=false;showAddModal.value=true}

    // 买入弹窗中输入股票代码时自动触发三看确认
    watch(()=>addForm.value.ts_code,(code)=>{if(code&&code.length>=6)loadThreeViews(code)},{delay:500})

    async function submitPosition(){
        addError.value='';const f=addForm.value;
        if(!f.ts_code.trim()){addError.value='请输入股票代码';return}if(!f.buy_price||f.buy_price<=0){addError.value='请输入有效的买入价格';return}if(!f.buy_volume||f.buy_volume<=0){addError.value='请输入有效的买入数量';return}
        submitting.value=true;
        try{const url=editingPosition.value?`${API}/api/positions/${editingPosition.value.id}/trades`:`${API}/api/positions`;const r=await fetchWithAuth(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(f)});const d=await r.json();if(r.ok){showAddModal.value=false;showToast(d.message||'保存成功');resetForm();await fetchData()}else{addError.value=d.error||'保存失败'}}catch(e){addError.value='网络错误'}finally{submitting.value=false}
    }

    // 确认买入：检查用户确认
    async function submitPositionWithConfirm(){
        addError.value='';
        const f=addForm.value;
        if(!f.ts_code.trim()){addError.value='请输入股票代码';return}
        if(!f.buy_price||f.buy_price<=0){addError.value='请输入有效的买入价格';return}
        if(!f.buy_volume||f.buy_volume<=0){addError.value='请输入有效的买入数量';return}
        // 检查用户确认（与卖出界面保持一致）
        if(threeViewsCheck.value&&!buyConfirmedCheck.value){
            addError.value='请先勾选确认框，确认已查看三看检查结果';return
        }
        await submitPosition();
    }

    function openSellModal(p){sellTarget.value=p;sellForm.value={sell_price:p.current_price||'',sell_volume:'',sell_date:new Date().toISOString().slice(0,10),fee:0,note:'',reason:''};sellError.value='';sellConfirmedCheck.value=false;showSellModal.value=true;loadSellCheck(p.id)}
    async function loadSellCheck(pid){sellCheckLoading.value=true;try{const r=await fetchWithAuth(`${API}/api/positions/${pid}/sell-check`);if(r.ok)sellCheckData.value=await r.json();else sellCheckData.value=null}catch(e){console.error('卖出检查加载失败:',e);sellCheckData.value=null}finally{sellCheckLoading.value=false}}
    function pickPrice(price){sellForm.value.sell_price=price}
    function pickBuyPrice(price){if(price)addForm.value.buy_price=price}
    function quickSell(mode){const t=sellTarget.value;if(!t||!mode)return;if(mode==='full'){sellForm.value.sell_volume=t.total_volume;sellForm.value.sell_price=t.current_price||sellForm.value.sell_price}else if(mode==='half'){sellForm.value.sell_volume=Math.floor(t.total_volume/2)}}
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
    function setStopLoss(price){alertForm.value.stop_loss=price.toFixed(3)}
    function setTakeProfit(price){alertForm.value.stop_profit=price.toFixed(3)}

    function openDetailModal(p){detailPosition.value=p;showDetailModal.value=true;loadKline(p.ts_code)}
    function closeDetailModal(){showDetailModal.value=false;if(klineChart){klineChart.dispose();klineChart=null}}
    function openTradeDetailModal(t){tradeDetailTarget.value=t;showTradeDetailModal.value=true}
    function closeTradeDetailModal(){showTradeDetailModal.value=false;tradeDetailTarget.value=null}
    async function loadThreeViews(ts_code){
        if(!ts_code){threeViewsCheck.value=null;return}
        threeViewsLoading.value=true;threeViewsError.value='';
        try{
            const r=await fetchWithAuth(`${API}/api/check-three-views?ts_code=${ts_code}`);
            if(r.ok){threeViewsCheck.value=await r.json()}else{const errData=await r.json().catch(()=>({}));threeViewsCheck.value=null;threeViewsError.value=errData.error||'数据获取失败'}
        }catch(e){console.error('三看确认加载失败:',e);threeViewsCheck.value=null;threeViewsError.value='网络异常，请检查连接'}finally{threeViewsLoading.value=false}
    }
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
    function toggleActionMenu(id){actionMenuOpen.value=actionMenuOpen.value===id?null:id;}
    function closeActionMenu(){actionMenuOpen.value=null;}

    function saveCapital(){fetchWithAuth(`${API}/api/capital`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(capitalForm.value)}).then(r=>r.json()).then(d=>{if(d.message){showToast(d.message);fetchData()}}).catch(()=>showToast('保存失败'))}
    function openCapitalModal(){fetchCapital();nextTick(()=>{capitalForm.value={...capitalForm.value};showCapitalModal.value=true})}
    function saveCapitalFromModal(){saveCapital();showCapitalModal.value=false}

    function switchToTradeLog(){activeTab.value='tradelog';fetchTradeLog();fetchCapital();nextTick(()=>renderAnalysisCharts())}
    function switchToAnalysis(){activeTab.value='analysis';fetchTradeLog();nextTick(()=>renderAnalysisCharts())}
    function switchToScreener(){activeTab.value='screener';loadScreenResult();loadAllStrategySummaries();}
    function switchToReview(){activeTab.value='review';loadReview(reviewPeriod.value)}
    function switchToStrategy(){activeTab.value='strategy';loadRegime();loadStrategyPerformance()}
    function selectStrategy(key){
        currentStrategy.value=key;
        // 从缓存恢复该策略的结果（如果有的话）
        if(allScreenResults.value[key]){
            const cached=allScreenResults.value[key];
            if(cached.results)screenResults.value=cached.results;
            if(cached.stats)screenStats.value=cached.stats;
            if(cached.info)screenInfo.value=cached.info;
            if(cached.market)screenMarket.value=cached.market;
        }
    };

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
    async function loadStrategyPerformance(force=false){
        // 缓存检查：5分钟内不重复请求（除非强制刷新）
        if(!force&&spData.value&&spLoadedAt.value&&(Date.now()-spLoadedAt.value<SP_CACHE_TTL)){
            return; // 使用缓存数据
        }
        spLoading.value=true;spError.value='';
        try{
            const r=await fetchWithAuth(`${API}/api/strategy-performance`);const d=await r.json();
            if(d.error){spError.value=d.error;return}
            spData.value=d;
            spLoadedAt.value=Date.now();
            nextTick(()=>renderSPCharts());
        }catch(e){spError.value='加载策略效果失败'}finally{spLoading.value=false}
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

    // === K-line Technical Indicator Calculators ===
    function calcEMA(arr,period){
        const k=2/(period+1);
        const ema=[arr[0]];
        for(let i=1;i<arr.length;i++){ema.push(arr[i]*k+ema[i-1]*(1-k))}
        return ema;
    }
    function calcMACD(prices,fast=12,slow=26,signal=9){
        const emaFast=calcEMA(prices,fast),emaSlow=calcEMA(prices,slow);
        const dif=emaFast.map((v,i)=>v-emaSlow[i]);
        const dea=calcEMA(dif,signal);
        const macd=dif.map((v,i)=>(v-dea[i])*2);
        return {dif,dea,macd};
    }
    function calcKDJ(highs,lows,closes,n=9,m1=3,m2=3){
        const rsv=[];
        for(let i=0;i<closes.length;i++){
            if(i<n-1){rsv.push(null);continue}
            const hh=Math.max(...highs.slice(i-n+1,i+1));
            const ll=Math.min(...lows.slice(i-n+1,i+1));
            rsv.push(hh===ll?50:(closes[i]-ll)/(hh-ll)*100);
        }
        const K=[rsv[n-1]||50],D=[rsv[n-1]||50];
        for(let i=n;i<rsv.length;i++){
            K.push((2*D[D.length-1]+rsv[i])/3);
            D.push((2*D[D.length-1]+K[K.length-1])/3);
        }
        for(let i=0;i<n-1;i++){K.unshift(null);D.unshift(null)}
        const J=K.map((k,i)=>k===null?null:3*k-2*D[i]);
        return {k:K,d:D,j:J};
    }
    function calcRSI(prices,period=14){
        const rsi=[];
        for(let i=0;i<prices.length;i++){
            if(i<period){rsi.push(null);continue}
            let gain=0,loss=0;
            for(let j=i-period+1;j<=i;j++){
                const diff=prices[j]-prices[j-1];
                if(diff>0)gain+=diff;else loss+=-diff;
            }
            const avgGain=gain/period,avgLoss=loss/period;
            if(avgLoss===0)rsi.push(100);
            else rsi.push(100-(100/(1+avgGain/avgLoss)));
        }
        return rsi;
    }
    function calcBOLL(prices,period=20,stdMul=2){
        const ma=[],upper=[],lower=[];
        for(let i=0;i<prices.length;i++){
            if(i<period-1){ma.push(null);upper.push(null);lower.push(null);continue}
            const slice=prices.slice(i-period+1,i+1);
            const m=slice.reduce((a,b)=>a+b,0)/period;
            const variance=slice.reduce((a,b)=>a+Math.pow(b-m,2),0)/period;
            const s=Math.sqrt(variance);
            ma.push(m);upper.push(m+stdMul*s);lower.push(m-stdMul*s);
        }
        return {ma,upper,lower};
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
        const dates=klineData.value.dates||[];
        const klines=klineData.value.klines||[];
        const volumes=klineData.value.volumes||[];
        if(dates.length===0)return;
        const upColor='#ef4444',downColor='#22c55e';
        const closes=klines.map(k=>k[1]);
        const highs=klines.map(k=>k[3]);
        const lows=klines.map(k=>k[2]);

        // 均线数据
        const maData={
            ma5:klineData.value.ma5||[],
            ma10:klineData.value.ma10||[],
            ma20:klineData.value.ma20||[],
            ma60:klineData.value.ma60||[],
            ma120:klineData.value.ma120||[],
            ma250:klineData.value.ma250||[]
        };
        const maColors={ma5:'#ffffff',ma10:'#f59e0b',ma20:'#3b82f6',ma60:'#a855f7',ma120:'#22c55e',ma250:'#ef4444'};

        // 副图指标计算
        const indicator=klineIndicator.value;
        let subSeries=[];
        let subGridHeight='18%';
        let subTop='72%';
        let subYAxis={type:'value',gridIndex:1,scale:true,splitNumber:2,axisLabel:{color:'#8b8fa3',fontSize:9},splitLine:{show:false},axisLine:{show:false}};

        if(indicator==='MACD'){
            const macd=calcMACD(closes);
            subSeries=[
                {name:'DIF',type:'line',xAxisIndex:1,yAxisIndex:1,data:macd.dif,smooth:true,lineStyle:{width:1,color:'#f59e0b'},symbol:'none'},
                {name:'DEA',type:'line',xAxisIndex:1,yAxisIndex:1,data:macd.dea,smooth:true,lineStyle:{width:1,color:'#3b82f6'},symbol:'none'},
                {name:'MACD',type:'bar',xAxisIndex:1,yAxisIndex:1,data:macd.macd.map((v,i)=>({value:v,itemStyle:{color:v>=0?upColor:downColor}})),barMaxWidth:4}
            ];
        }else if(indicator==='KDJ'){
            const kdj=calcKDJ(highs,lows,closes);
            subSeries=[
                {name:'K',type:'line',xAxisIndex:1,yAxisIndex:1,data:kdj.k,smooth:true,lineStyle:{width:1,color:'#f59e0b'},symbol:'none'},
                {name:'D',type:'line',xAxisIndex:1,yAxisIndex:1,data:kdj.d,smooth:true,lineStyle:{width:1,color:'#3b82f6'},symbol:'none'},
                {name:'J',type:'line',xAxisIndex:1,yAxisIndex:1,data:kdj.j,smooth:true,lineStyle:{width:1,color:'#ef4444'},symbol:'none'}
            ];
            subYAxis={...subYAxis,max:100,min:0};
        }else if(indicator==='RSI'){
            const rsi=calcRSI(closes,14);
            subSeries=[
                {name:'RSI',type:'line',xAxisIndex:1,yAxisIndex:1,data:rsi,smooth:true,lineStyle:{width:1,color:'#a855f7'},symbol:'none',markLine:{silent:true,symbol:'none',data:[{yAxis:70,lineStyle:{color:'#ef4444',type:'dashed'}},{yAxis:30,lineStyle:{color:'#22c55e',type:'dashed'}}]}}
            ];
            subYAxis={...subYAxis,max:100,min:0};
        }else if(indicator==='BOLL'){
            const boll=calcBOLL(closes);
            // BOLL显示在主图上，副图回归成交量
            subSeries=[{type:'bar',xAxisIndex:1,yAxisIndex:1,data:volumes.map((v,i)=>({value:v,itemStyle:{color:klines[i]&&klines[i][1]>=klines[i][0]?upColor:downColor}})),barMaxWidth:4}];
        }else{
            // VOL (默认)
            subSeries=[{type:'bar',xAxisIndex:1,yAxisIndex:1,data:volumes.map((v,i)=>({value:v,itemStyle:{color:klines[i]&&klines[i][1]>=klines[i][0]?upColor:downColor}})),barMaxWidth:4}];
        }

        // 构建主图series
        const mainSeries=[{name:'K线',type:'candlestick',xAxisIndex:0,yAxisIndex:0,data:klines,itemStyle:{color:upColor,color0:downColor,borderColor:upColor,borderColor0:downColor}}];
        // 均线
        Object.keys(maVisibility.value).forEach(key=>{
            if(maVisibility.value[key]){
                const label=key.toUpperCase();
                mainSeries.push({name:label,type:'line',xAxisIndex:0,yAxisIndex:0,data:maData[key],smooth:true,lineStyle:{width:1,color:maColors[key]},symbol:'none',connectNulls:true});
            }
        });
        // BOLL主图叠加
        if(indicator==='BOLL'){
            const boll=calcBOLL(closes);
            mainSeries.push({name:'BOLL上轨',type:'line',xAxisIndex:0,yAxisIndex:0,data:boll.upper,smooth:true,lineStyle:{width:1,color:'#f59e0b',type:'dashed'},symbol:'none'});
            mainSeries.push({name:'BOLL中轨',type:'line',xAxisIndex:0,yAxisIndex:0,data:boll.ma,smooth:true,lineStyle:{width:1,color:'#ffffff'},symbol:'none'});
            mainSeries.push({name:'BOLL下轨',type:'line',xAxisIndex:0,yAxisIndex:0,data:boll.lower,smooth:true,lineStyle:{width:1,color:'#22c55e',type:'dashed'},symbol:'none'});
        }

        // 交易标记数据
        const tradeMarks=klineData.value.trades||[];
        const buyMarks=tradeMarks.filter(t=>t.type==='buy').map(t=>[t.date,t.price]);
        const sellMarks=tradeMarks.filter(t=>t.type==='sell').map(t=>[t.date,t.price]);

        // legend数据
        const legendData=['K线'];
        Object.keys(maVisibility.value).forEach(key=>{if(maVisibility.value[key])legendData.push(key.toUpperCase())});
        if(indicator==='BOLL')legendData.push('BOLL上轨','BOLL中轨','BOLL下轨');
        if(indicator==='MACD')legendData.push('DIF','DEA','MACD');
        if(indicator==='KDJ')legendData.push('K','D','J');
        if(indicator==='RSI')legendData.push('RSI');
        if(buyMarks.length)legendData.push('买入');
        if(sellMarks.length)legendData.push('卖出');

        // tooltip数据
        const pctChgs=klineData.value.pct_chgs||[];
        const signals=klineData.value.signals||[];
        const dayData=dates.map((d,i)=>{
            const k=klines[i]||[0,0,0,0];
            return{date:d,open:k[0],close:k[1],low:k[2],high:k[3],vol:volumes[i]||0,pct:pctChgs[i]||0,
                ma5:maData.ma5[i],ma10:maData.ma10[i],ma20:maData.ma20[i],ma60:maData.ma60[i],ma120:maData.ma120[i],ma250:maData.ma250[i],signals:signals[i]||[]};
        });

        // 构建series（主图+副图+交易标记）
        const allSeries=[...mainSeries,...subSeries];
        if(buyMarks.length){
            allSeries.push({
                name:'买入',type:'scatter',xAxisIndex:0,yAxisIndex:0,
                symbol:'triangle',symbolSize:14,data:buyMarks,
                itemStyle:{color:'#3b82f6'},
                label:{show:true,position:'top',formatter:'B',color:'#3b82f6',fontSize:10,fontWeight:'bold',distance:4},
                z:10
            });
        }
        if(sellMarks.length){
            allSeries.push({
                name:'卖出',type:'scatter',xAxisIndex:0,yAxisIndex:0,
                symbol:'triangle',symbolSize:14,symbolRotate:180,data:sellMarks,
                itemStyle:{color:'#ef4444'},
                label:{show:true,position:'bottom',formatter:'S',color:'#ef4444',fontSize:10,fontWeight:'bold',distance:4},
                z:10
            });
        }

        // 策略信号 markPoint — 简洁圆点标记，避免与K线/交易标记重叠
        const buySignalPoints=[];
        const sellSignalPoints=[];
        dates.forEach((d,i)=>{
            const sigs=signals[i]||[];
            const high=klines[i][3];
            const low=klines[i][2];
            sigs.forEach(s=>{
                if(s.type==='buy'){
                    // 买入信号放在K线下方，用向上箭头
                    buySignalPoints.push({name:s.label,xAxis:d,yAxis:low});
                }else{
                    // 卖出信号放在K线上方，用向下箭头
                    sellSignalPoints.push({name:s.label,xAxis:d,yAxis:high});
                }
            });
        });
        if(buySignalPoints.length){
            allSeries.push({
                name:'买点',type:'scatter',xAxisIndex:0,yAxisIndex:0,
                data:buySignalPoints.map(p=>[p.xAxis,p.yAxis]),
                symbol:'triangle',symbolSize:10,
                itemStyle:{color:'#3b82f6',opacity:0.9},
                label:{show:true,position:'bottom',fontSize:8,color:'#3b82f6',formatter:(p,i)=>buySignalPoints[i]?.name||'',distance:2},
                z:8
            });
        }
        if(sellSignalPoints.length){
            allSeries.push({
                name:'卖点',type:'scatter',xAxisIndex:0,yAxisIndex:0,
                data:sellSignalPoints.map(p=>[p.xAxis,p.yAxis]),
                symbol:'triangle',symbolSize:10,symbolRotate:180,
                itemStyle:{color:'#ef4444',opacity:0.9},
                label:{show:true,position:'top',fontSize:8,color:'#ef4444',formatter:(p,i)=>sellSignalPoints[i]?.name||'',distance:2},
                z:8
            });
        }

        klineChart.setOption({
            backgroundColor:'transparent',
            tooltip:{
                trigger:'axis',
                confine:true,
                backgroundColor:'rgba(30,33,48,0.95)',
                borderColor:'#3b82f6',
                borderWidth:1,
                textStyle:{color:'#e2e8f0',fontSize:12},
                axisPointer:{type:'cross',label:{backgroundColor:'#3b82f6',color:'#fff'}},
                formatter:function(params){
                    const idx=params[0]?.dataIndex;if(idx===undefined||!dayData[idx])return'';
                    const d=dayData[idx];
                    const pctColor=d.pct>0?'#ef4444':(d.pct<0?'#22c55e':'#8b8fa3');
                    const pctSign=d.pct>0?'+':'';
                    let html=`<div style="font-weight:700;margin-bottom:4px">${d.date} <span style="color:${pctColor};margin-left:8px">${pctSign}${d.pct}%</span></div>`+
                        `<div>开 <span style="color:#f59e0b">¥${d.open}</span> &nbsp;收 <span style="color:#f59e0b">¥${d.close}</span> &nbsp;低 <span style="color:#22c55e">¥${d.low}</span> &nbsp;高 <span style="color:#ef4444">¥${d.high}</span></div>`+
                        `<div style="margin-top:4px">量 ${d.vol}手`;
                    if(d.ma5!=null)html+=` &nbsp;MA5 <span style="color:#fff">¥${d.ma5}</span>`;
                    if(d.ma10!=null)html+=` &nbsp;MA10 <span style="color:#f59e0b">¥${d.ma10}</span>`;
                    if(d.ma20!=null)html+=` &nbsp;MA20 <span style="color:#3b82f6">¥${d.ma20}</span>`;
                    if(d.ma60!=null)html+=` &nbsp;MA60 <span style="color:#a855f7">¥${d.ma60}</span>`;
                    if(d.ma120!=null)html+=` &nbsp;MA120 <span style="color:#22c55e">¥${d.ma120}</span>`;
                    if(d.ma250!=null)html+=` &nbsp;MA250 <span style="color:#ef4444">¥${d.ma250}</span>`;
                    html+='</div>';
                    // 策略信号提示
                    if(d.signals&&d.signals.length){
                        html+=`<div style="margin-top:6px;border-top:1px solid #2a2e3f;padding-top:4px">`;
                        d.signals.forEach(s=>{
                            const color=s.type==='buy'?'#3b82f6':'#ef4444';
                            html+=`<span style="color:${color};font-weight:700">${s.label}</span> <span style="color:#8b8fa3;font-size:11px">${s.desc}</span> &nbsp;`;
                        });
                        html+='</div>';
                    }
                    // 交易标记提示
                    const dayTrades=tradeMarks.filter(t=>t.date===d.date);
                    if(dayTrades.length){
                        html+=`<div style="margin-top:6px;border-top:1px solid #2a2e3f;padding-top:4px">`;
                        dayTrades.forEach(t=>{
                            const color=t.type==='buy'?'#3b82f6':'#ef4444';
                            const label=t.type==='buy'?'买入':'卖出';
                            html+=`<span style="color:${color};font-weight:700">${label}</span> ¥${t.price} × ${t.volume}股 &nbsp;`;
                        });
                        html+='</div>';
                    }
                    return html;
                }
            },
            legend:{data:legendData,top:0,textStyle:{color:'#8b8fa3',fontSize:10},itemWidth:14,itemHeight:8},
            grid:[{left:50,right:10,top:32,height:'55%'},{left:50,right:10,top:'72%',height:'18%'}],
            xAxis:[{type:'category',data:dates,gridIndex:0,axisLabel:{color:'#8b8fa3',fontSize:10},axisLine:{lineStyle:{color:'#2a2e3f'}},splitLine:{show:false}},{type:'category',data:dates,gridIndex:1,axisLabel:{show:false},axisLine:{lineStyle:{color:'#2a2e3f'}}}],
            yAxis:[{type:'value',gridIndex:0,scale:true,splitNumber:4,axisLabel:{color:'#8b8fa3',fontSize:10,formatter:v=>'¥'+v.toFixed(2)},splitLine:{lineStyle:{color:'#1e2130'}},axisLine:{show:false}},subYAxis],
            dataZoom:[{type:'inside',xAxisIndex:[0,1],start:50,end:100},{type:'slider',xAxisIndex:[0,1],start:50,end:100,bottom:2,height:14,textStyle:{color:'#8b8fa3',fontSize:9},borderColor:'#2a2e3f',fillerColor:'rgba(59,130,246,0.15)',handleStyle:{color:'#3b82f6'}}],
            series:allSeries
        },true); // true = notMerge, 完全重置
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
            const r=await fetchWithAuth(`${API}/api/screen/status?strategy=${currentStrategy.value}`);
            if(!r.ok){
                // 【v3.5.4 修复】API调用失败时清除定时器，避免永久轮询
                console.error('轮询状态API失败:', r.status, await r.text());
                clearInterval(screenPollTimer);screenPollTimer=null;
                showToast('状态查询失败，请刷新页面');
                return;
            }
            const d=await r.json();
            if(!d.running){
                clearInterval(screenPollTimer);screenPollTimer=null;
                loadScreenResult();
            }
        }catch(e){
            // 【v3.5.4 修复】网络错误也清除定时器，避免永久轮询
            console.error('轮询状态网络错误:', e);
            clearInterval(screenPollTimer);screenPollTimer=null;
            showToast('网络连接异常，请检查网络');
        }
    }
    async function loadScreenResult(){
        try{
            const r=await fetchWithAuth(`${API}/api/screen/result?strategy=${currentStrategy.value}`);
            if(!r.ok){
                console.error('加载结果API失败:', r.status, await r.text());
                showToast('加载结果失败，请刷新页面');
                return;
            }
            const d=await r.json();
            screenMarket.value=d.market||null;screenStats.value=d.stats||null;screenResults.value=d.results||[];
            screenHistory.value=d.history||[];
            screenInfo.value={running:d.running||false,hasResult:!!d.results&&d.results.length>0,lastRun:d.screen_time||null,runTime:d.run_time||null};
            // ★ 缓存到多策略结果池
            allScreenResults.value[currentStrategy.value]={market:d.market||null,stats:d.stats||null,results:d.results||[],info:screenInfo.value,history:d.history||[]};
            // ★ 更新该策略的摘要
            if(d.results&&d.results.length>0){
                const topScore=d.results.reduce((max,r)=>Math.max(max,r.total_score||r.total||0),0);
                strategySummaries.value[currentStrategy.value]={
                    count:d.results.length,
                    time:d.screen_time||'',
                    duration:d.run_time||0,
                    topScore:topScore,
                    marketStatus:d.market?(d.market.trend||d.market.regime||''):'',
                    hasResult:true
                };
                // 从运行列表中移除
                runningStrategies.value=runningStrategies.value.filter(s=>s!==currentStrategy.value);
            }
            if(d.error)showToast('选股出错: '+d.error);
        }catch(e){
            console.error('加载结果网络错误:', e);
            showToast('网络异常，无法加载结果');
        }
    }
    // ★ 加载所有策略的执行摘要（供策略卡片展示）
    async function loadAllStrategySummaries(){
        try{
            const r=await fetchWithAuth(`${API}/api/screen/all-summaries`);const d=await r.json();
            if(d.summaries)strategySummaries.value=d.summaries;
            if(d.running)runningStrategies.value=d.running;
        }catch(e){}
    }
    // ★ 单策略摘要获取器
    const getStrategySummary=computed(()=>(key)=>strategySummaries.value[key]||null);
    // ★ 切换到某策略并查看其缓存结果
    function viewStrategyResult(key){selectStrategy(key);}
    // ★ 选择并立即运行
    function selectAndRun(key){selectStrategy(key);startScreen();}
    async function loadStrategies(){
        try{
            const r=await fetchWithAuth(`${API}/api/screen/strategies`);const d=await r.json();
            if(d.strategies&&Object.keys(d.strategies).length>0){
                // [UI-03 修复] 合并而非覆盖：保留默认值，用 API 数据补充/更新
                Object.keys(d.strategies).forEach(key=>{
                    const merged = Object.assign({}, strategyListSafe[key] || {}, d.strategies[key]);
                    strategyList.value[key] = merged;
                    // 同时更新普通对象
                    strategyListSafe[key] = merged;
                });
                // 根据大盘环境自动设置推荐策略
                if(d.recommended&&d.reason){
                    currentStrategy.value=d.recommended;
                    screenStrategyRecommended.value=d.recommended;
                    screenStrategyReason.value=d.reason;
                }
            }
        }catch(e){
            // [UI-04 修复] API 失败时保留默认值，不覆盖
            console.log('[loadStrategies] API 失败，使用默认策略列表');
        }
    }
    loadStrategies();
    const screenBonusTags=computed(()=>{const tags=new Set();screenResults.value.forEach(r=>{(r.bonus_details||[]).forEach(t=>tags.add(t))});return [...tags]});
    // 选股参数预览
    const getScreenPreview=computed(()=>{
        const strategy=currentStrategy.value;
        const params=screenParams.value||[];
        // 辅助函数：安全读取参数值
        function pv(key, fallback){ return params.find(p=>p.key===key)?.value??fallback }
        const preview=[];
        if(strategy==='trend_break'){
            const mvMin=pv('min_circ_mv',50), mvMax=pv('max_circ_mv',300);
            const devMin=pv('ma20_deviation_min',0);
            const volMin=pv('min_vol_ratio',1.2);
            preview.push({text:`MA20站稳+方向向上+高低点抬高（偏离≥${devMin}%）`,highlight:true});
            preview.push({text:'MACD(12分) 金叉/零上/柱增强',highlight:true});
            preview.push({text:'均线形态(10分) MA5>MA10>MA20多头排列',highlight:true});
            preview.push({text:'量比 ≥ '+volMin+'（放量确认）',highlight:volMin<=1.5});
            preview.push({text:'共振(15分) 量价+均线+HL三看≥2项通过',highlight:false});
            preview.push({text:'催化(10分) 业绩预告/回购/涨停基因',highlight:false});
            preview.push({text:`市值 ${mvMin}-${mvMax}亿 + 板块超额 + 资金净流入`,highlight:true});
        }else if(strategy==='sector_leader'){
            const boardPct=pv('min_board_pct',2);
            const turnMin=pv('min_turnover',8);
            const mvMin=pv('min_circ_mv',50), mvMax=pv('max_circ_mv',200);
            preview.push({text:`Step1: 概念板块涨幅 Top5（≥${boardPct}% 热门门槛）`,highlight:true});
            preview.push({text:`Step2: 每个板块内个股涨幅 Top5`,highlight:true});
            preview.push({text:`换手率 ≥ ${turnMin}%（资金充分换手）`,highlight:true});
            preview.push({text:`流通市值 ${mvMin}-${mvMax}亿（适中弹性）`,highlight:true});
            preview.push({text:'排除ST / 上市不足60日次新股',highlight:false});
            preview.push({text:'二值判断：板块第1名=龙头，其余=观察',highlight:true});
        }else if(strategy==='oversold_bounce'){
            const dropThr=Math.abs(pv('min_drop_pct',20));
            const mvMin=pv('min_circ_mv',100);
            const mvMax=pv('max_circ_mv',0);
            const techMin=pv('tech_confirm_min',1);
            const volThr=pv('vol_ratio_threshold',1.3);
            const shadowThr=pv('lower_shadow_pct',1.5);
            // 过滤门禁
            preview.push({text:`🔒 过滤：非ST + 上市≥90天 + 跌幅>${dropThr}% + 市值≥${mvMin}亿${mvMax>0?' ≤'+mvMax+'亿':'(不限)'}`,highlight:true});
            // 止跌信号（3选1+即可）
            preview.push({text:`🛑 止跌信号：长下影线>${shadowThr}% / 放量阳线>1%(${volThr}倍量比) / MACD底部金叉`,highlight:true});
            // 技术改善（3选N）
            preview.push({text:`⚡ 技术改善：MA5上穿MA20金叉 + 显著放量≥${Math.max(volThr*1.5,2).toFixed(1)}倍 + 阳线>0.5%`,highlight:techMin>=1});
            if(techMin>=1)preview.push({text:`   → 要求至少${techMin}个技术改善信号`,highlight:false});
            // HL结构
            preview.push({text:`📊 HL结构：低点抬高=反弹就绪(+10) / 继续创新低=-5⚠️`,highlight:false});
            // 评分维度
            preview.push({text:`📐 评分：趋势(35) + 板块(15/5) + 资金(15) + 加分(15) + 技术(15) + HL(-5~10) = 满分100`,highlight:true});
        }
        return preview;
    });
    function quickBuyFromScreen(r){resetForm();addForm.value.ts_code=r.ts_code;const _p=Number(r.price)||0;addForm.value.buy_price=_p>0?_p:'';addForm.value.buy_volume=100;showAddModal.value=true;if(r.ts_code&&r.ts_code.length>=6) loadThreeViews(r.ts_code)}

    // 【v3.3.1】筛选审计追踪（v3.6支持自选股查看）
    const showAuditModal=ref(false)
    const auditStock=ref(null)
    const auditData=ref(null)
    function showAuditDetail(r){
        // 统一处理选股结果和自选股两种数据结构
        auditStock.value={ts_code:r.ts_code,name:r.name||r.ts_code}
        auditData.value=r.match_audit||null  // 后端返回的完整审计数据
        if(!auditData.value) {
            // 区分：手动添加 vs 旧版筛选结果
            auditData.value={
                error:'该股票是手动添加的，无筛选审计记录',
                gates:[],scoring:{},metrics:{},
                is_manual:true  // 标记为手动添加，用于UI区分显示
            }
        } else {
            auditData.value.is_manual=false
        }
        showAuditModal.value=true
    }
    function hlLabel(s){
        const m={
            'rebound_ready':'反弹就绪','stabilizing':'初步企稳',
            'weak_uptrend':'弱势反弹','strong_uptrend':'强势反弹',
            'downtrend_continues':'继续创新低','uncertain':'不确定',
            'insufficient_data':'数据不足','error':'计算异常'
        }
        return m[s]||s||'—'
    }

    // 审计弹窗指标网格辅助函数（适配三套策略不同metrics字段，v3.6完善）
    function metricLabel(key){
        const m={
            // 基础指标
            pct_20d:'近20日跌幅', deviation:'MA20偏离度', price_change_pct:'日涨幅', pct_chg:'涨跌幅',
            circ_mv_yi:'流通市值', total_net_in:'主力净流入', inflow_days:'连续流入天数',
            inflow_ratio:'流入强度比', excess_return:'相对超额',
            vol_ratio:'量比', turnover_rate:'换手率',
            // 超跌反弹特有
            hl_structure:'HL结构', macd_signal:'MACD信号',
            three_views_count:'三看通过数',
            stop_signals:'止跌信号', tech_improve_signals:'技术改善信号',
            matched_concepts:'匹配概念',
        }
        return m[key]||key
    }

    // HL结构标签中文映射
    function hlStructureLabel(val){
        const m={
            'rebound_ready':'反弹就绪',
            'stabilizing':'初步企稳',
            'weak_uptrend':'弱势反弹',
            'strong_uptrend':'强势反弹',
            'downtrend_continues':'继续创新低',
            'uncertain':'不确定',
            'insufficient_data':'数据不足',
            'error':'计算异常'
        }
        return m[val]||val||'—'
    }

    // MACD信号标签中文映射
    function macdSignalLabel(val){
        const m={
            'bullish_trend':'多头信号',
            'bearish_trend':'空头信号',
            'neutral':'中性',
            'golden_cross':'金叉',
            'dead_cross':'死叉',
        }
        return m[val]||val||'—'
    }

    function metricFormat(key, val){
        if(val===null || val===undefined) return '—'
        if(key==='circ_mv_yi'){
            const n=parseFloat(val)
            if(n>=10000) return (n/10000).toFixed(1)+'万亿'
            return n.toFixed(0)+'亿'
        }
        // 主力净流入：智能格式化万/亿
        if(key==='total_net_in' && typeof val==='number'){
            const absVal=Math.abs(val)
            let formatted
            if(absVal>=10000){
                formatted=(val/10000).toFixed(2)+'亿'
            } else {
                formatted=val.toFixed(0)+'万'
            }
            return (val>=0?'+':'')+formatted
        }
        // HL结构和MACD信号：中文标签
        if(key==='hl_structure') return hlStructureLabel(val)
        if(key==='macd_signal') return macdSignalLabel(val)
        // 百分比类
        if(['pct_20d','deviation','price_change_pct','pct_chg','excess_return','inflow_ratio','vol_ratio','turnover_rate'].includes(key)){
            const num=typeof val==='number'?val:parseFloat(val)
            if(isNaN(num)) return String(val)
            return num.toFixed(2)+'%'
        }
        if(key.match(/(pct|ratio)/)) return val+'%'
        // 数组
        if(Array.isArray(val)) return val.slice(0,4).join(', ')||'无'
        // 数字
        if(typeof val==='number') return val
        return String(val)
    }

    function metricColorClass(key, val){
        // 涨跌类：正红负绿（A股惯例：红涨绿跌）
        if(['pct_20d','price_change_pct','pct_chg','excess_return','deviation'].includes(key)){
            const num=typeof val==='number'?val:parseFloat(val)
            if(isNaN(num)) return ''
            return num>0?'text-red':(num<0?'text-green':'')
        }
        // 净流入：正红（流入）负绿（流出）
        if(key==='total_net_in'){ return (val>=0)?'text-red':'text-green' }
        // HL结构：根据类型着色
        if(key==='hl_structure'){
            if(['rebound_ready','strong_uptrend','stabilizing'].includes(val)) return 'text-green'
            if(['downtrend_continues'].includes(val)) return 'text-red'
            return 'text-blue'
        }
        // MACD信号：多头红 空头绿
        if(key==='macd_signal'){
            if(['bullish_trend','golden_cross'].includes(val)) return 'text-red'
            if(['bearish_trend','dead_cross'].includes(val)) return 'text-green'
            return 'text-blue'
        }
        // 特殊颜色
        if(key==='matched_concepts') return 'text-purple'
        if(['stop_signals','tech_improve_signals','three_views_count'].includes(key)) return 'text-blue'
        return ''
    }

    // 观察池
    async function addToWatch(r){
        try{
            const res=await fetchWithAuth(`${API}/api/watch-list`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ts_code:r.ts_code,name:r.name,price:r.price,strategy:currentStrategy.value,total_score:r.total_score,match_audit:r.match_audit||null})}); // v3.6: 同步审计轨迹
            const d=await res.json();
            showToast(d.message||'操作完成');
        }catch(e){showToast('添加失败')}
    }
    async function addAllToWatch(){
        if(!screenResults.value.length)return;
        if(watchAdding.value)return; // 防止重复点击
        watchAdding.value=true;
        try{
            // 只加入Top 10到观察池，v3.6同步match_audit审计轨迹
            const stocks=screenResults.value.slice(0,10).map(r=>({ts_code:r.ts_code,name:r.name,price:r.price,strategy:currentStrategy.value,total_score:r.total_score,match_audit:r.match_audit||null}));
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
    const filterNames={all:'全部',profit:'盈利',loss:'亏损',high_score:'高分',trend_break:'趋势',sector_leader:'龙头',oversold_bounce:'超跌'};
    const watchReportData=ref(null);
    const watchLastUpdate=ref('');

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
        const items=filteredWatchItems.value;
        let profit=0,loss=0,total=0,noData=0,flat=0;
        const validItems=[];
        items.forEach(w=>{
            const c=w.track_chg_pct;
            if(c==null||c===undefined||c===''){noData++;return;}
            validItems.push(c);
            total+=c;
            if(c>0)profit++;
            else if(c<0)loss++;
            else flat++;
        });
        const validCount=validItems.length;
        const denom=(profit+loss+flat)||1; // 含平盘的总数，避免除零
        // ★ 修复：胜率基于有效数据的盈利占比（含平盘时分母更准确）
        const winRate=validCount>0?Math.round(profit/denom*100):0;
        // ★ 修复：平均收益只对有数据项计算
        const avgChg=validCount>0?Math.round(total/validCount*100)/100:0;
        return{
            profit,loss,noData,flat,
            win_rate:winRate,
            avg_chg:avgChg,
            count:validCount,
            totalCount:watchItems.value.length,
            // 前端可用来显示当前筛选状态
                        filterLabel:watchFilter.value!=='all'?`(${filterNames[watchFilter.value]||watchFilter.value})`:''};
    });
    const watchGroups=computed(()=>{const s=new Set();watchItems.value.forEach(w=>{if(w.tag)s.add(w.tag)});return[...s].sort()});
    const filteredWatchItems=computed(()=>{
        let list=[...watchItems.value];
        const f=watchFilter.value;
        if(f!=='all'){
            if(f==='profit')list=list.filter(w=>(w.track_chg_pct||0)>0);
            else if(f==='loss')list=list.filter(w=>(w.track_chg_pct||0)<0);
            else if(f==='high_score')list=list.filter(w=>(w.add_score||0)>=60);
            else if(f==='trend_break'||f==='sector_leader'||f==='oversold_bounce')list=list.filter(w=>w.add_strategy===f);
        }
        if(watchSearch.value.trim()){const kw=watchSearch.value.trim().toLowerCase();list=list.filter(w=>(w.name||'').toLowerCase().includes(kw)||w.ts_code.toLowerCase().includes(kw)||(w.tag||'').toLowerCase().includes(kw)||(w.add_strategy||'').toLowerCase().includes(kw))}
        return list;
    });

    function switchToWatch(){activeTab.value='watch';loadWatchList()}
    async function refreshWatchData(){
        if(watchLoading.value)return;
        await loadWatchList();
        showToast('行情已刷新');
    }
    async function loadWatchList(){
        watchLoading.value=true;
        try{
            var _api=API+'/api/watch-list';
            const r=await fetchWithAuth(_api);const d=await r.json();
            watchItems.value=d.items||[];watchListCount.value=d.count||0;
            const now=new Date();
            var _h=String(now.getHours()).padStart(2,'0'),_m=String(now.getMinutes()).padStart(2,'0');
            watchLastUpdate.value=_h+':'+_m;
        }catch(e){watchItems.value=[]}finally{watchLoading.value=false}
    }
    async function removeWatchItem(w){
        if(!confirm(`确认移除 ${w.name||w.ts_code}？`))return;
        try{
            const code = w.ts_code;
            const r=await fetchWithAuth(`${API}/api/watch-list/${encodeURIComponent(code)}`,{method:'DELETE'});
            if(!r.ok){ const err=await r.json().catch(()=>({error:'删除失败'})); showToast(err.error||'删除失败'); return; }
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
        resetForm();
        addForm.value.ts_code=w.ts_code;
        // ★ 修复：自动填充价格（优先当前价>加入价>0），避免显示0.000
        const _price=Number(w.current_price)||Number(w.add_price)||0;
        addForm.value.buy_price=_price>0?_price:'';
        // ★ 修复：默认填入100股，用户可修改
        addForm.value.buy_volume=100;
        showAddModal.value=true;
        // ★ 立即触发三看检查（不依赖500ms延迟watcher）
        if(w.ts_code && w.ts_code.length>=6) loadThreeViews(w.ts_code);
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

    // === Trade Plan Functions (v3.7 P0优化) ===
    const tradePlans=ref([]);const tradePlanLoading=ref(false);const tradePlanFilter=ref('all');
    const tradePlanModal=ref(false);const tradePlanEditing=ref(null);
    const tradePlanForm=ref({ts_code:'',name:'',plan_type:'buy',target_price:'',trigger_price:'',planned_volume:100,planned_amount:'',strategy:'',reason:'',due_date:''});
    const tradePlanSearch=ref('');const tradePlanSearchResults=ref([]);let tradePlanSearchTimer=null;
    const filteredTradePlans=computed(()=>{
        let list=tradePlans.value||[];
        if(tradePlanFilter.value!=='all')list=list.filter(p=>p.status===tradePlanFilter.value);
        if(tradePlanSearch.value.trim()){const kw=tradePlanSearch.value.trim().toLowerCase();list=list.filter(p=>(p.name||'').toLowerCase().includes(kw)||p.ts_code.toLowerCase().includes(kw)||(p.strategy||'').toLowerCase().includes(kw));}
        return list;
    });
    function switchToTradePlan(){activeTab.value='tradeplan';loadTradePlans();}
    async function loadTradePlans(){tradePlanLoading.value=true;try{const r=await fetchWithAuth(`${API}/api/trade-plans`);const d=await r.json();tradePlans.value=d||[];}catch(e){tradePlans.value=[];}finally{tradePlanLoading.value=false;}}
    async function searchTradePlanStock(){clearTimeout(tradePlanSearchTimer);const kw=tradePlanForm.value.ts_code.trim();if(kw.length<1){tradePlanSearchResults.value=[];return;}tradePlanSearchTimer=setTimeout(async()=>{try{const r=await fetchWithAuth(`${API}/api/search?keyword=${encodeURIComponent(kw)}`);const d=await r.json();tradePlanSearchResults.value=d.results||[];}catch(e){tradePlanSearchResults.value=[];}},300);}
    function selectTradePlanStock(s){tradePlanForm.value.ts_code=s.ts_code;tradePlanForm.value.name=s.name;tradePlanSearchResults.value=[];}
    async function submitTradePlan(){const f=tradePlanForm.value;if(!f.ts_code.trim()){showToast('请输入股票代码');return;}try{const body={...f,ts_code:f.ts_code.trim().toUpperCase(),target_price:parseFloat(f.target_price)||null,trigger_price:parseFloat(f.trigger_price)||null,planned_volume:parseInt(f.planned_volume)||0,planned_amount:parseFloat(f.planned_amount)||null};if(tradePlanEditing.value){const r=await fetchWithAuth(`${API}/api/trade-plans/${tradePlanEditing.value.id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();showToast(d.message||'已更新');}else{const r=await fetchWithAuth(`${API}/api/trade-plans`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();showToast(d.message||'已创建');}tradePlanModal.value=false;tradePlanEditing.value=null;tradePlanForm.value={ts_code:'',name:'',plan_type:'buy',target_price:'',trigger_price:'',planned_volume:100,planned_amount:'',strategy:'',reason:'',due_date:''};await loadTradePlans();}catch(e){showToast('保存失败');}}
    function openTradePlanModal(plan){if(plan){tradePlanEditing.value=plan;tradePlanForm.value={ts_code:plan.ts_code,name:plan.name||'',plan_type:plan.plan_type,target_price:plan.target_price||'',trigger_price:plan.trigger_price||'',planned_volume:plan.planned_volume||100,planned_amount:plan.planned_amount||'',strategy:plan.strategy||'',reason:plan.reason||'',due_date:plan.due_date||''};}else{tradePlanEditing.value=null;tradePlanForm.value={ts_code:'',name:'',plan_type:'buy',target_price:'',trigger_price:'',planned_volume:100,planned_amount:'',strategy:'',reason:'',due_date:''};}tradePlanModal.value=true;}
    async function doDeleteTradePlan(plan){if(!confirm(`确认删除 ${plan.name||plan.ts_code} 的交易计划？`))return;try{const r=await fetchWithAuth(`${API}/api/trade-plans/${plan.id}`,{method:'DELETE'});const d=await r.json();showToast(d.message);await loadTradePlans();}catch(e){showToast('删除失败');}}
    async function doExecuteTradePlan(plan){const price=prompt(`请输入 ${plan.name||plan.ts_code} 的实际执行价格：`,plan.target_price||'');if(price===null)return;try{const r=await fetchWithAuth(`${API}/api/trade-plans/${plan.id}/execute`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({executed_price:parseFloat(price)||null})});const d=await r.json();showToast(d.message);await loadTradePlans();}catch(e){showToast('操作失败');}}
    async function doCancelTradePlan(plan){if(!confirm(`确认取消 ${plan.name||plan.ts_code} 的交易计划？`))return;try{const r=await fetchWithAuth(`${API}/api/trade-plans/${plan.id}/cancel`,{method:'POST'});const d=await r.json();showToast(d.message);await loadTradePlans();}catch(e){showToast('操作失败');}}
    async function checkTradePlanAlerts(){try{const r=await fetchWithAuth(`${API}/api/trade-plans/check`);const d=await r.json();if(d.triggered&&d.triggered.length>0){d.triggered.forEach(t=>showToast(t.message));await loadTradePlans();}}catch(e){}}
    const tradePlanStatusText={'pending':'待触发','triggered':'已触发','executed':'已执行','cancelled':'已取消','expired':'已过期'};
    const tradePlanStatusClass={'pending':'badge-info','triggered':'badge-warning','executed':'badge-success','cancelled':'badge-muted','expired':'badge-muted'};
    const tradePlanTypeText={'buy':'买入','sell':'卖出'};
    const tradePlanTypeClass={'buy':'text-red','sell':'text-green'};

    // === Price Alert Functions (v3.7 P0优化) ===
    const priceAlerts=ref([]);const priceAlertLoading=ref(false);const priceAlertModal=ref(false);
    const priceAlertForm=ref({ts_code:'',name:'',alert_type:'price_change_pct',threshold:5,direction:'above',note:''});
    const priceAlertSearch=ref('');const priceAlertSearchResults=ref([]);let priceAlertSearchTimer=null;
    const alertNotifications=ref([]);const showAlertCenter=ref(false);const alertCenterLoading=ref(false);
    async function loadPriceAlerts(){priceAlertLoading.value=true;try{const r=await fetchWithAuth(`${API}/api/price-alerts`);const d=await r.json();priceAlerts.value=d||[];}catch(e){priceAlerts.value=[];}finally{priceAlertLoading.value=false;}}
    async function searchPriceAlertStock(){clearTimeout(priceAlertSearchTimer);const kw=priceAlertForm.value.ts_code.trim();if(kw.length<1){priceAlertSearchResults.value=[];return;}priceAlertSearchTimer=setTimeout(async()=>{try{const r=await fetchWithAuth(`${API}/api/search?keyword=${encodeURIComponent(kw)}`);const d=await r.json();priceAlertSearchResults.value=d.results||[];}catch(e){priceAlertSearchResults.value=[];}},300);}
    function selectPriceAlertStock(s){priceAlertForm.value.ts_code=s.ts_code;priceAlertForm.value.name=s.name;priceAlertSearchResults.value=[];}
    async function submitPriceAlert(){const f=priceAlertForm.value;if(!f.ts_code.trim()){showToast('请输入股票代码');return;}try{const body={...f,ts_code:f.ts_code.trim().toUpperCase(),threshold:parseFloat(f.threshold)||0};const r=await fetchWithAuth(`${API}/api/price-alerts`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();showToast(d.message||'已创建');priceAlertModal.value=false;priceAlertForm.value={ts_code:'',name:'',alert_type:'price_change_pct',threshold:5,direction:'above',note:''};await loadPriceAlerts();}catch(e){showToast('创建失败');}}
    async function doDeletePriceAlert(alert){if(!confirm(`确认删除 ${alert.name||alert.ts_code} 的价格预警？`))return;try{const r=await fetchWithAuth(`${API}/api/price-alerts/${alert.id}`,{method:'DELETE'});const d=await r.json();showToast(d.message);await loadPriceAlerts();}catch(e){showToast('删除失败');}}
    async function checkAllAlerts(){alertCenterLoading.value=true;const notifications=[];try{const r1=await fetchWithAuth(`${API}/api/alerts/check`);const d1=await r1.json();if(d1.triggered)notifications.push(...d1.triggered);}catch(e){}try{const r2=await fetchWithAuth(`${API}/api/trade-plans/check`);const d2=await r2.json();if(d2.triggered)notifications.push(...d2.triggered);}catch(e){}try{const r3=await fetchWithAuth(`${API}/api/price-alerts/check`);const d3=await r3.json();if(d3.triggered)notifications.push(...d3.triggered);}catch(e){}alertNotifications.value=notifications;alertCenterLoading.value=false;if(notifications.length>0){notifications.forEach(n=>showToast(n.message));}return notifications.length;}
    const alertTypeText={'price_change_pct':'涨跌幅','price_break':'价格突破','volume_spike':'成交量异动'};
    const alertDirectionText={'above':'高于','below':'低于'};

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
            stockFinanceHealth.value={score:0,level:'无数据',levelClass:'',roe:0,gross:0,debt:0,breakdown:{profit:0,efficiency:0,safety:0,growth:0}};
            return;
        }
        
        const roe=fina.roe||0;
        const gross=fina.grossprofit_margin||0;
        const net=fina.netprofit_margin||0;
        const debt=fina.debt_to_assets||0;
        const eps=fina.eps||0;
        const orYoy=fina.or_yoy||0;
        const profitYoy=fina.yoy_profit||fina.q_profit||0;
        
        // 四维度评分（满分100）
        // 1. 盈利能力（0-30分）- ROE + 净利率
        let profitScore=0;
        profitScore+=roe>=0.20?15:roe>=0.15?12:roe>=0.10?9:roe>=0.05?5:roe>0?2:0;
        profitScore+=net>=0.20?15:net>=0.15?12:net>=0.10?9:net>=0.05?5:net>0?2:0;
        
        // 2. 运营效率（0-25分）- 毛利率 + EPS
        let efficiencyScore=0;
        efficiencyScore+=gross>=0.40?15:gross>=0.30?12:gross>=0.20?9:gross>=0.10?5:gross>0?2:0;
        efficiencyScore+=eps>=2?10:eps>=1?8:eps>=0.5?5:eps>0?2:0;
        
        // 3. 财务安全（0-25分）- 负债率（越低越好）
        let safetyScore=0;
        safetyScore+=debt<=0.30?25:debt<=0.40?20:debt<=0.50?15:debt<=0.60?10:debt<=0.70?5:0;
        
        // 4. 成长能力（0-20分）- 营收同比 + 净利同比
        let growthScore=0;
        growthScore+=orYoy>=0.30?10:orYoy>=0.20?8:orYoy>=0.10?6:orYoy>=0?3:orYoy>=-0.10?1:0;
        growthScore+=profitYoy>=0.30?10:profitYoy>=0.20?8:profitYoy>=0.10?6:profitYoy>=0?3:profitYoy>=-0.10?1:0;
        
        const totalScore=profitScore+efficiencyScore+safetyScore+growthScore;
        
        let level='';
        let levelClass='';
        if(totalScore>=80){level='优秀';levelClass='excellent';}
        else if(totalScore>=60){level='良好';levelClass='good';}
        else if(totalScore>=40){level='一般';levelClass='average';}
        else{level='较差';levelClass='poor';}
        
        stockFinanceHealth.value={
            score:Math.round(totalScore),
            level,
            levelClass,
            roe:roe,
            gross:gross,
            debt:debt,
            breakdown:{
                profit:Math.round(profitScore),
                efficiency:Math.round(efficiencyScore),
                safety:Math.round(safetyScore),
                growth:Math.round(growthScore)
            }
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
        // 后端 income API 返回 revenue(元), n_income(元) — 转为亿
        const revenues=incomeData.map(d=>(d.revenue||0)/100000000).reverse();
        const profits=incomeData.map(d=>(d.n_income||d.total_profit||0)/100000000).reverse();
        
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
    
    // 渲染ROE趋势图
    function renderRoeTrendChart(finaData){
        const el=document.getElementById('roeTrendChart');
        if(!el||!finaData?.length)return;
        if(window._roeTrendChart)window._roeTrendChart.dispose();
        const chart=echarts.init(el);
        window._roeTrendChart=chart;
        
        const dates=finaData.map(d=>d.end_date).slice(0,8).reverse();
        const roes=finaData.map(d=>(d.roe||0)*100).slice(0,8).reverse();
        
        chart.setOption({
            backgroundColor:'transparent',
            tooltip:{trigger:'axis',formatter:p=>`${p[0].name}<br/>ROE: ${p[0].value?.toFixed(1)}%`},
            grid:{left:0,right:0,top:5,bottom:0},
            xAxis:{type:'category',data:dates,show:false},
            yAxis:{type:'value',show:false,min:0},
            series:[{
                type:'line',
                data:roes,
                smooth:true,
                symbol:'circle',
                symbolSize:6,
                lineStyle:{width:2,color:'#ef4444'},
                itemStyle:{color:'#ef4444'},
                areaStyle:{
                    color:new echarts.graphic.LinearGradient(0,0,0,1,[
                        {offset:0,color:'rgba(239,68,68,0.3)'},
                        {offset:1,color:'rgba(239,68,68,0)'}
                    ])
                }
            }]
        });
    }
    
    // 渲染营收增长趋势图
    function renderRevenueTrendChart(finaData){
        const el=document.getElementById('revenueTrendChart');
        if(!el||!finaData?.length)return;
        if(window._revenueTrendChart)window._revenueTrendChart.dispose();
        const chart=echarts.init(el);
        window._revenueTrendChart=chart;
        
        const dates=finaData.map(d=>d.end_date).slice(0,8).reverse();
        const yoys=finaData.map(d=>((d.or_yoy||0)*100)).slice(0,8).reverse();
        
        chart.setOption({
            backgroundColor:'transparent',
            tooltip:{trigger:'axis',formatter:p=>`${p[0].name}<br/>营收同比: ${p[0].value>=0?'+':''}${p[0].value?.toFixed(1)}%`},
            grid:{left:0,right:0,top:5,bottom:0},
            xAxis:{type:'category',data:dates,show:false},
            yAxis:{type:'value',show:false},
            series:[{
                type:'bar',
                data:yoys,
                barWidth:'60%',
                itemStyle:{
                    color:p=>p.value>=0?'#10b981':'#ef4444',
                    borderRadius:[3,3,0,0]
                }
            }]
        });
    }
    
    // 监听财务子Tab切换
    watch(stockFinanceSubTab,(newVal)=>{
        if(newVal==='income'&&stockFinanceData.value?.income_trend?.length){
            nextTick(()=>renderStockIncomeChart(stockFinanceData.value.income_trend));
        }
    });
    
    // 监听财务数据加载完成，渲染趋势图
    watch(stockFinanceData,(newVal)=>{
        if(newVal?.fina_indicator?.length){
            nextTick(()=>{
                renderRoeTrendChart(newVal.fina_indicator);
                renderRevenueTrendChart(newVal.fina_indicator);
            });
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
    // 策略数据计算属性（用于顶部市场情绪卡片）
    const strategyData=computed(()=>{
        if(!regimeData.value)return{};
        const r=regimeData.value;
        return{
            market_status:r.regime||'--',
            score:r.regime_score||0,
            position_suggestion:r.strategy?.max_position||'--',
            trend_strength:r.indicators?.ma20>r.indicators?.ma60?'多头':'空头',
            action_direction:r.strategy?.action||'--',
            stop_loss:r.strategy?.stop_loss||'--',
            take_profit:r.strategy?.take_profit||'--',
            key_points:r.strategy?.tips?r.strategy.tips.join('；'):'--'
        }
    });
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
    let currentRefreshInterval=8000;
    
    // 判断是否为交易时段（9:15-15:00）
    function isTradeTime(){const now=new Date();const h=now.getHours();const m=now.getMinutes();const day=now.getDay();if(day===0||day===6)return false;if((h===9&&m>=15)||(h>=10&&h<15))return true;return false}
    
    // 获取智能刷新间隔：交易时段30秒，非交易时段5分钟
    function getRefreshInterval(){return isTradeTime()?30000:300000}
    
    function startAutoRefresh(){
        currentRefreshInterval=getRefreshInterval();
        refreshTimer=setInterval(async()=>{
            if(isLoggedIn.value){
                await fetchData();
                await fetchIndex();
                // 检查是否需要调整刷新频率（时段切换时）
                const newInterval=getRefreshInterval();
                if(newInterval!==currentRefreshInterval){
                    clearInterval(refreshTimer);
                    currentRefreshInterval=newInterval;
                    startAutoRefresh();
                }
            }
        },currentRefreshInterval)
    }
    function handleResize(){pieChart&&pieChart.resize();barChart&&barChart.resize();emotionChart&&emotionChart.resize();winRateChart&&winRateChart.resize();profitChart&&profitChart.resize();klineChart&&klineChart.resize();compareNormChart&&compareNormChart.resize();comparePriceChart&&comparePriceChart.resize();spWinRateChart&&spWinRateChart.resize();spAvgChgChart&&spAvgChgChart.resize()}

    async function fetchCurrentUser(){
        if(!isLoggedIn.value)return;
        try{
            const r=await fetchWithAuth(`${API}/api/auth/me`);
            if(r.ok){
                const d=await r.json();
                currentUser.value=d.username||d.nickname||'';
                // 同时更新 localStorage 保持同步
                localStorage.setItem(USER_KEY,currentUser.value);
            }
        }catch(e){/* 静默失败，使用 localStorage 中的缓存 */}
    }

    onMounted(()=>{if(isLoggedIn.value){fetchCurrentUser();fetchData();fetchIndex();fetchCapital();loadRegime()}startTokenRefresh();startAutoRefresh();window.addEventListener('resize',handleResize)});
    onUnmounted(()=>{clearInterval(refreshTimer);clearInterval(screenPollTimer);if(alertAutoTimer.value)clearInterval(alertAutoTimer.value);window.removeEventListener('resize',handleResize);pieChart&&pieChart.dispose();barChart&&barChart.dispose();emotionChart&&emotionChart.dispose();winRateChart&&winRateChart.dispose();profitChart&&profitChart.dispose();klineChart&&klineChart.dispose();compareNormChart&&compareNormChart.dispose();comparePriceChart&&comparePriceChart.dispose();spWinRateChart&&spWinRateChart.dispose();spAvgChgChart&&spAvgChgChart.dispose()});

    return{isLoggedIn,currentUser,showAuthModal,authMode,authError,authSubmitting,loginForm,registerForm,doLogin,doRegister,doLogout,fetchCurrentUser,
        // 适老化字体大小
        fontSize,setFontSize,
        positions,summary,loading,searchKeyword,sortKey,sortDir,activeTab,indexData,indexSource,indexTime,tradeLogs,tradeLogStats,capitalForm,strategyData,
        screenMarket,screenStats,screenResults,screenHistory,screenInfo,screenBonusTags,getScreenPreview,
        allScreenResults,strategySummaries,runningStrategies,getStrategySummary,
        currentStrategy,strategyList,strategyListSafe,currentStrategyMeta,screenStrategyReason,screenStrategyRecommended,strategyBacktestData,expandedDetails,toggleDetails,
        viewStrategyResult,selectAndRun,loadAllStrategySummaries,
        showAddModal,showSellModal,showDetailModal,showAlertModal,showImportModal,showParamsModal,showAdviceModal,showHeaderMenu,showTradeDetailModal,editingPosition,detailPosition,sellTarget,alertTarget,adviceTarget,tradeDetailTarget,
        emotionLabels,volumePriceHelper,addForm,sellForm,alertForm,addError,sellError,submitting,submittingSell,searchResults,importData,importMode,importError,
        toastMsg,sellPreview,filteredPositions,priceLevels,priceLevelsLoading,priceLevelsError,adviceData,adviceLoading,adviceError,actionMenuOpen,
        threeViewsCheck,threeViewsLoading,threeViewsError,loadThreeViews,
        sellCheckData,sellCheckLoading,sellConfirmedCheck,loadSellCheck,pickPrice,quickSell,
        buyConfirmedCheck,
        reviewPeriod,reviewLoading,reviewData,regimeLoading,regimeData,showStrategyDetail,backtestDays,backtestHold,backtestLoading,backtestData,alertCheckLoading,alertCheckResult,alertAutoMode,alertAutoTimer,toggleAlertAuto,
        klineData,klineLoading,klineIndicator,maVisibility,renderKlineChart,
        screenParams,paramsLoading,paramsDefault,forceMode,spLoading,spData,spError,spLoadedAt,loadStrategyPerformance,
        refreshData,searchStock,selectStock,submitPosition,openAddModal,openTradeModal,
        openSellModal,submitSell,submitPositionWithConfirm,openAlertModal,submitAlerts,clearAlerts,setStopLoss,setTakeProfit,openDetailModal,closeDetailModal,openTradeDetailModal,closeTradeDetailModal,openAdviceModal,quickAddFromAdvice,quickSellFromAdvice,confirmDelete,deleteTrade,toggleActionMenu,closeActionMenu,
        saveCapital,openCapitalModal,saveCapitalFromModal,showCapitalModal,switchToTradeLog,switchToAnalysis,switchToScreener,switchToReview,switchToStrategy,selectStrategy,
        loadReview,loadRegime,runBacktest,checkAlerts,loadKline,
        positionAdvice,loadPositionAdvice,showPositionAdviceDetail,
        startScreen,startScreenForce,pollScreenResult,loadScreenResult,quickBuyFromScreen,
        // 【v3.3.1 修复】筛选审计弹窗 - 必须return才能在模板中使用
        showAuditModal,auditStock,auditData,showAuditDetail,hlLabel,metricLabel,metricColorClass,metricFormat,
        addToWatch,addAllToWatch,showWatchReport,watchAdding,watchList,watchReport,
        watchItems,watchLoading,watchListCount,watchSearch,watchFilter,watchReportData,
        watchAddModal,watchAddForm,watchAddSearch,watchTagModal,watchTagTarget,watchTagForm,
        watchNoteModal,watchNoteTarget,watchNoteForm,
        showWatchAdviceModal,watchAdviceTarget,watchAdviceData,watchAdviceLoading,watchAdviceError,
        showWatchStrategyModal,watchStrategyTarget,watchStrategyData,watchStrategyLoading,watchStrategyError,
        watchStats,watchLastUpdate,watchGroups,filteredWatchItems,
        switchToWatch,refreshWatchData,loadWatchList,removeWatchItem,clearWatchList,loadWatchReport,
        searchWatchStock,selectWatchStock,submitWatchAdd,quickBuyFromWatch,
        openWatchTagModal,submitWatchTag,openWatchNoteModal,submitWatchNote,
        openAdviceModalForWatch,openWatchStrategyModal,openKlineModalForWatch,translateStrategy,
        resetParams,onParamChange,confirmRunScreen,
        // Trade Plan (v3.7 P0)
        tradePlans,tradePlanLoading,tradePlanFilter,tradePlanModal,tradePlanEditing,tradePlanForm,tradePlanSearch,tradePlanSearchResults,filteredTradePlans,
        switchToTradePlan,loadTradePlans,searchTradePlanStock,selectTradePlanStock,submitTradePlan,openTradePlanModal,doDeleteTradePlan,doExecuteTradePlan,doCancelTradePlan,checkTradePlanAlerts,
        tradePlanStatusText,tradePlanStatusClass,tradePlanTypeText,tradePlanTypeClass,
        // Price Alert (v3.7 P0)
        priceAlerts,priceAlertLoading,priceAlertModal,priceAlertForm,priceAlertSearch,priceAlertSearchResults,
        alertNotifications,showAlertCenter,alertCenterLoading,
        loadPriceAlerts,searchPriceAlertStock,selectPriceAlertStock,submitPriceAlert,doDeletePriceAlert,checkAllAlerts,
        alertTypeText,alertDirectionText,
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
        openStockFinanceModal,calculateFinanceHealth,renderStockIncomeChart,renderRoeTrendChart,renderRevenueTrendChart,
        financeKpiClass,financeKpiTag,financeKpiTagClass,
        formatAmount,
        getAlertsTooltip,
        // 选股结果展开行
        expandedScreenRow,toggleScreenRow};
}
}).mount('#app');
