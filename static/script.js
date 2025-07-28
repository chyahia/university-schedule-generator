// ================== المحتوى الكامل والصحيح لملف script.js ==================

// =================================================================================
// --- Global Variables ---
// =================================================================================
let currentScheduleData = {
    schedule: null,
    days: [],
    slots: [],
    swapped_ids: []
};
let currentScheduleByProfessor = null;
let currentFreeRoomsSchedule = null;
let selectedTeacher = null;
let availableLevelsForBuilder = [];
let availableLargeRooms = [];
let allAvailableTeachers = [];
let allAvailableCourses = [];

// =================================================================================
// --- Initial Setup on Page Load ---
// =================================================================================
window.addEventListener('DOMContentLoaded', () => {
    loadInitialData();
    setupEventListeners();
    setupModalListeners();
    setupScheduleBuilder();
    setupBackupRestoreListeners();
    setupDataImportExportListeners();
    setupSettingsManagementListeners();
    setupFlexCategoriesListeners();
});


// =================================================================================
// --- Core Data Loading and UI Generation ---
// =================================================================================
function loadInitialData() {
    Promise.all([
        fetch('/teachers').then(res => res.json()),
        fetch('/students').then(res => res.json()),
        fetch('/rooms').then(res => res.json()),
        fetch('/api/levels').then(res => res.json())
    ])
    .then(([teachers, courses, rooms, levels]) => {
        
        availableLevelsForBuilder = levels;
        availableLargeRooms = rooms.filter(r => r.type === 'كبيرة');
        populateConsecutiveHallDropdown();
        allAvailableTeachers = teachers;
        allAvailableCourses = courses;
        populateLevelLargeRoomAssignment(levels, availableLargeRooms);
        updateAllLargeRoomDropdowns();

        const teacherData = new Map();
        
        teachers.forEach(t => {
            if (t && t.name) {
                teacherData.set(t.name, { count: 0, courses: [] });
            }
        });
        courses.forEach(course => {
            if (course.teacher_name && teacherData.has(course.teacher_name)) {
                let data = teacherData.get(course.teacher_name);
                data.count++;
                data.courses.push(course.name);
            }
        });
        
        createProfessorDaysTable(teachers, teacherData);

        const teacherList = document.getElementById('teacher-list');
        teacherList.innerHTML = ''; 
    
        teachers.forEach(teacher => {
            if (!teacher || !teacher.name) return;

            const li = document.createElement('li');
            const data = teacherData.get(teacher.name) || { count: 0, courses: [] };

            const entryDiv = document.createElement('div');
            entryDiv.className = 'teacher-entry';

            const nameSpan = document.createElement('span');
            nameSpan.className = 'teacher-name';
            nameSpan.textContent = teacher.name + ' ';

            const countSpan = document.createElement('span');
            countSpan.className = 'course-count';
            countSpan.textContent = `(${data.count})`;
            
            nameSpan.appendChild(countSpan);
            entryDiv.appendChild(nameSpan);

            if (data.count > 0) {
                const dropdownBtn = document.createElement('button');
                dropdownBtn.className = 'courses-dropdown-btn';
                dropdownBtn.title = 'عرض المواد المسندة';
                dropdownBtn.innerHTML = '▼';
                entryDiv.appendChild(dropdownBtn);
            }
            
            li.appendChild(entryDiv);

            if (data.count > 0) {
                const dropdownList = document.createElement('ul');
                dropdownList.className = 'courses-dropdown-list';
                data.courses.forEach(courseName => {
                    const courseLi = document.createElement('li');
                    courseLi.textContent = courseName;
                    dropdownList.appendChild(courseLi);
                });
                li.appendChild(dropdownList);
                li.classList.add('teacher-assigned');
            }

            teacherList.appendChild(li);
        });
    
        const courseList = document.getElementById('course-list');
        courseList.innerHTML = '';
        courses.forEach(course => {
            const li = document.createElement('li');
            li.dataset.courseId = course.id;
            li.textContent = `${course.name} (${course.levels.join('، ')})`; 
            if (course.teacher_name) {
                li.classList.add('assigned');
            }
            courseList.appendChild(li);
        });
    
        populateManagementLists(teachers, rooms, courses, levels);
        populateSaturdayTeachersSelect(teachers);
        populateLastSlotRestrictionUI(teachers);
        populateLevelDropdowns(levels);
        loadAndBuildIdentifiersTable();
        populateFlexCategoryLevelSelect(levels);
        loadSettingsAndBuildUI();
    })
    .catch(error => console.error('خطأ في تحميل البيانات الأولية:', error));
}

function populateLevelDropdowns(levels) {
    // --- الجزء الخاص بالقوائم المنسدلة الأخرى يبقى كما هو ---
    const levelDropdowns = [
        document.getElementById('bulk-course-level') // قد يكون هذا العنصر محذوفاً الآن، لكن نتركه لتجنب الأخطاء
    ];
    levelDropdowns.forEach(dropdown => {
        if (!dropdown) return;
        const defaultValue = dropdown.options[0];
        dropdown.innerHTML = '';
        dropdown.appendChild(defaultValue);
        levels.forEach(level => {
            const option = document.createElement('option');
            option.value = level;
            option.textContent = level;
            dropdown.appendChild(option);
        });
    });

    // --- ✨ بداية الإضافة الجديدة: ملء حاوية مربعات الاختيار ---
    const levelsContainer = document.getElementById('bulk-course-levels-container');
    if (levelsContainer) {
        levelsContainer.innerHTML = ''; // مسح المحتوى الافتراضي
        if (levels.length === 0) {
            levelsContainer.innerHTML = '<p>الرجاء إضافة مستويات أولاً.</p>';
        } else {
            levels.forEach(level => {
                const label = document.createElement('label');
                label.style.display = 'block';
                label.innerHTML = `<input type="checkbox" name="bulk_levels" value="${level}" style="margin-left: 8px;"> ${level}`;
                levelsContainer.appendChild(label);
            });
        }
    }
}

function populateFlexCategoryLevelSelect(levels) {
    const levelSelect = document.getElementById('flex-category-level-select');
    if (!levelSelect) return;

    // مسح الخيارات القديمة مع الحفاظ على الخيار الافتراضي
    while (levelSelect.options.length > 1) {
        levelSelect.remove(1);
    }

    levels.forEach(level => {
        levelSelect.add(new Option(level, level));
    });
}

function createProfessorDaysTable(teachers, teacherData) {
    const container = document.getElementById('professor-days-table-container');
    if (!container) return;
    container.innerHTML = '';

    const days = ['الأحد', 'الإثنين', 'الثلاثاء', 'الأربعاء', 'الخميس'];
    
    // --- بداية التعديل: إضافة العمود الجديد ---
    const timeConstraintsHeaders = ['بدء ح2', 'بدء ح3', 'إنهاء بـ ح3', 'إنهاء بـ ح4', 'بدء ح2 وإنهاء ح4'];
    const timeConstraintsKeys = ['start_d1_s2', 'start_d1_s3', 'end_s3', 'end_s4', 'always_s2_to_s4'];
    // --- نهاية التعديل ---
    
    const distributionRules = {
        'غير محدد': 'غير محدد (مرن)',
        'يومان متتاليان': 'تجميع في يومين',
        'ثلاثة أيام متتالية': 'تجميع في 3 أيام',
        'يومان منفصلان': 'يومان منفصلان',
        'ثلاثة ايام منفصلة': 'ثلاثة أيام منفصلة'
    };

    const table = document.createElement('table');
    table.style.width = '100%';
    table.style.borderCollapse = 'collapse';
    table.style.fontSize = '16px';

    const thead = table.createTHead();
    const headerRow = thead.insertRow();
    
    const thStyle = 'border: 1px solid #ddd; padding: 6px; background-color: #f2f2f2;';
    headerRow.innerHTML = `<th style="${thStyle}">الأستاذ</th>` +
                        days.map(day => `<th style="${thStyle}">${day}</th>`).join('') +
                        timeConstraintsHeaders.map(header => `<th style="${thStyle}" title="${header}">${header}</th>`).join('') +
                        `<th style="${thStyle}">قاعدة التوزيع</th>`;
    
    const tbody = table.createTBody();

    teachers.forEach(teacher => {
        if (!teacher || !teacher.name) return;
        const row = tbody.insertRow();
        const teacherName = teacher.name;
        const data = teacherData.get(teacherName) || { count: 0 };
        row.insertCell().textContent = `${teacherName} (${data.count})`;

        days.forEach(day => {
            const cell = row.insertCell();
            cell.style.textAlign = 'center';
            cell.innerHTML = `<input type="checkbox" data-teacher="${teacherName}" data-day="${day}">`;
        });

        timeConstraintsKeys.forEach(key => {
            const cell = row.insertCell();
            cell.style.textAlign = 'center';
            // --- بداية التعديل: إضافة عنوان وصفي للعمود الجديد ---
            const titleText = key === 'always_s2_to_s4' ? 'تطبيق القيد على كل أيام عمل الأستاذ' : '';
            cell.innerHTML = `<input type="checkbox" data-teacher="${teacherName}" data-constraint="${key}" title="${titleText}">`;
            // --- نهاية التعديل ---
        });

        const ruleCell = row.insertCell();
        const select = document.createElement('select');
        select.dataset.teacher = teacherName;
        select.dataset.ruleType = 'distribution';
        select.style.width = '100%';
        
        for (const [value, text] of Object.entries(distributionRules)) {
            select.options.add(new Option(text, value));
        }
        ruleCell.appendChild(select);
    });

    container.appendChild(table);
}


// استبدل الدالة القديمة بالكامل بهذه النسخة المصححة
function collectAllCurrentSettings() {
    // --- جمع كل الإعدادات الفردية أولاً ---
    const manual_days_to_save = {};
    document.querySelectorAll('#professor-days-table-container input[data-day]:checked').forEach(cb => {
        const teacher = cb.dataset.teacher;
        const day = cb.dataset.day;
        if (!manual_days_to_save[teacher]) manual_days_to_save[teacher] = [];
        manual_days_to_save[teacher].push(day);
    });

    const special_constraints_to_save = {};
    document.querySelectorAll('#professor-days-table-container input[data-constraint]:checked').forEach(cb => {
        const teacher = cb.dataset.teacher;
        const constraint = cb.dataset.constraint;
        if (!special_constraints_to_save[teacher]) special_constraints_to_save[teacher] = {};
        special_constraints_to_save[teacher][constraint] = true;
    });
    document.querySelectorAll('#professor-days-table-container select[data-rule-type="distribution"]').forEach(select => {
        const teacher = select.dataset.teacher;
        if (!special_constraints_to_save[teacher]) special_constraints_to_save[teacher] = {};
        special_constraints_to_save[teacher]['distribution_rule'] = select.value;
    });

    const saturday_teachers_to_save = Array.from(document.querySelectorAll('#saturday-teachers-checkbox-container input:checked')).map(cb => cb.value);
    const last_slot_restrictions_to_save = {};
    document.querySelectorAll('.last-slot-restriction-select').forEach(select => {
        if (select.value !== 'none') {
            last_slot_restrictions_to_save[select.dataset.teacherName] = select.value;
        }
    });

    const level_specific_large_rooms_to_save = {};
    document.querySelectorAll('#level-large-room-assignment-container select').forEach(select => {
        const level = select.dataset.level;
        const roomName = select.value;
        if (level && roomName) {
            level_specific_large_rooms_to_save[level] = roomName;
        }
    });

    const specific_small_room_assignments_to_save = {};
    document.querySelectorAll('.specific-room-assignment-row').forEach(row => {
        const courseSelect = row.querySelector('.course-select');
        const roomSelect = row.querySelector('.small-room-select');
        if (courseSelect && roomSelect) {
            const course = courseSelect.value;
            const room = roomSelect.value;
            if (course && room) {
                specific_small_room_assignments_to_save[course] = room;
            }
        }
    });

    const flexible_categories_to_save = [];
    document.querySelectorAll('.flex-category').forEach(catDiv => {
        const category = {
            id: catDiv.dataset.categoryId,
            level: catDiv.dataset.level,
            name: catDiv.querySelector('.flex-category-header input').value,
            professors: [],
            courses: catDiv.querySelector('.flex-courses textarea').value.split('\n').map(c => c.trim()).filter(Boolean)
        };
        catDiv.querySelectorAll('.professor-quota-row').forEach(row => {
            const teacherName = row.querySelector('select').value;
            const quota = parseInt(row.querySelector('input').value, 10);
            if (teacherName && quota > 0) {
                category.professors.push({ name: teacherName, quota: quota });
            }
        });
        flexible_categories_to_save.push(category);
    });
    
    const algorithm_settings = {
        method: document.querySelector('input[name="scheduling_method"]:checked').value,
        timeout: document.getElementById('timeout-input').value,
        tabu_iterations: document.getElementById('tabu-iterations-input').value,
        tabu_tenure: document.getElementById('tabu-tenure-input').value,
        tabu_neighborhood_size: document.getElementById('tabu-neighborhood-size-input').value,
        ga_population_size: document.getElementById('ga-population-input').value,
        ga_generations: document.getElementById('ga-generations-input').value,
        ga_mutation_rate: document.getElementById('ga-mutation-input').value,
        ga_elitism_count: document.getElementById('ga-elitism-input').value,
        lns_iterations: document.getElementById('lns-iterations-input').value,
        lns_ruin_factor: document.getElementById('lns-ruin-factor-input').value,
        vns_iterations: document.getElementById('vns-iterations-input').value,
        vns_k_max: document.getElementById('vns-k-max-input').value,
        ma_population_size: document.getElementById('ma-population-input').value,
        ma_generations: document.getElementById('ma-generations-input').value,
        ma_mutation_rate: document.getElementById('ma-mutation-input').value,
        ma_elitism_count: document.getElementById('ma-elitism-input').value,
        ma_local_search_iterations: document.getElementById('ma-local-search-iterations').value,
        clonalg_population_size: document.getElementById('clonalg-population-input').value,
        clonalg_generations: document.getElementById('clonalg-generations-input').value,
        clonalg_selection_size: document.getElementById('clonalg-selection-input').value,
        clonalg_clone_factor: document.getElementById('clonalg-clone-factor-input').value,
        hh_iterations: document.getElementById('hh-iterations-input').value,
        hh_selected_llh: Array.from(document.querySelectorAll('input[name="hh_llh_select"]:checked')).map(cb => cb.value),
        hh_tabu_tenure: document.getElementById('hh-tabu-tenure-input').value,
        hh_budget_mode: document.querySelector('input[name="hh_budget_mode"]:checked').value,
        hh_time_budget: document.getElementById('hh-time-budget-input').value,
        hh_llh_iterations: document.getElementById('hh-llh-iterations-input').value,
        hh_stagnation_limit: document.getElementById('hh-stagnation-limit-input').value,
        max_sessions_per_day: document.getElementById('max-sessions-per-day-select').value,
        consecutive_large_hall_rule: document.getElementById('consecutive-large-hall-select').value,
        intensive_search_attempts: document.getElementById('intensive-search-attempts').value,
        distribution_rule_type: document.querySelector('input[name="distribution_rule_type"]:checked').value,
        prioritize_primary: document.getElementById('prioritize-primary-slots-cb').checked,
        teacher_pairs_text: document.getElementById('teacher-pairs-textarea').value
    };

    // --- ✨ بناء الكائن النهائي بالهيكل الصحيح ✨ ---
    return {
        schedule_structure: collectScheduleStructureFromUI(),
        phase_5_settings: {
            rest_periods: {
                tuesday_evening: document.getElementById('tuesday-evening-rest').checked,
                thursday_evening: document.getElementById('thursday-evening-rest').checked,
            },
            manual_days: manual_days_to_save,
            special_constraints: special_constraints_to_save,
            saturday_teachers: saturday_teachers_to_save,
            last_slot_restrictions: last_slot_restrictions_to_save,
            level_specific_large_rooms: level_specific_large_rooms_to_save,
            specific_small_room_assignments: specific_small_room_assignments_to_save
        },
        flexible_categories: flexible_categories_to_save,
        algorithm_settings: algorithm_settings
    };
}

// ✅ الصق هذه النسخة الكاملة والمصححة مكان الدالة القديمة
function setupEventListeners() {
    
    // تعريف المتغيرات مرة واحدة في بداية الدالة
    const logOutput = document.getElementById('log-output');
    const generateBtn = document.getElementById('generate-schedule-button');
    const stopBtn = document.getElementById('stop-generation-button');
    let eventSource = null; // متغير للاحتفاظ باتصال SSE

    // --- مستمعات خاصة بالخوارزمية والمتابعة الحية ---
    generateBtn.addEventListener('click', function() {
        // 1. تجهيز الواجهة
        const progressBarContainer = document.getElementById('progress-container');
        const progressBar = document.getElementById('progress-bar');
        
        if(progressBar) {
            progressBar.style.width = '0%';
            progressBar.textContent = '0%';
            progressBar.style.backgroundColor = '#dc3545';
            progressBarContainer.style.display = 'none';
        }
        document.getElementById('timetables-output-area').innerHTML = ''; 
        logOutput.textContent = 'بدء الاتصال بالخادم وإرسال الإعدادات...\n';
        logOutput.style.display = 'block';
        generateBtn.style.display = 'none';
        stopBtn.style.display = 'inline-block';
        stopBtn.disabled = false;
        stopBtn.textContent = '🛑 إيقاف البحث';

        // 2. جمع الإعدادات والتحقق منها
        const settings = collectAllCurrentSettings();
        if (Object.keys(settings.schedule_structure).length === 0 || !Object.values(settings.schedule_structure).some(day => Object.keys(day).length > 0)) {
            alert('الرجاء إضافة يوم دراسي واحد على الأقل مع فترة زمنية واحدة في المرحلة 4.');
            resetGenerationUI();
            logOutput.style.display = 'none';
            return;
        }

        // 3. إرسال طلب بدء الجدولة أولاً
        fetch('/api/generate-schedule', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        })
        .then(response => {
            if (!response.ok) { 
                return response.json().then(err => { throw new Error(err.error || 'فشل في بدء العملية') });
            }
            return response.json();
        })
        .then(data => {
            if (data.status === 'ok') {
                // 4. فقط بعد نجاح البدء، نقوم بالاستماع للسجلات
                logOutput.textContent += 'تم بدء العملية بنجاح. جاري استقبال سجل المتابعة...\n';
                eventSource = new EventSource('/stream-logs');

                eventSource.onmessage = function(event) {
                    const message = event.data;
                    if (message.startsWith("DONE")) {
                        const jsonData = message.substring(4);
                        const data = JSON.parse(jsonData);
                        
                        logOutput.textContent += '\n--- انتهت العملية! ---\n';
                        try {
                            currentScheduleData.schedule = data.schedule;
                            currentScheduleData.days = data.days;
                            currentScheduleData.slots = data.slots;
                            currentScheduleData.swapped_ids = data.swapped_lecture_ids || [];
                            
                            const schedulePayload = { schedule: data.schedule, days: data.days, slots: data.slots };
                            fetch('/api/schedules/by-professor', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(schedulePayload) }).then(res => res.json()).then(profSchedules => { currentScheduleByProfessor = profSchedules; });
                            fetch('/api/schedules/free-rooms', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(schedulePayload) }).then(res => res.json()).then(freeRooms => { currentFreeRoomsSchedule = freeRooms; });
                            
                            const mainOutputArea = document.getElementById('timetables-output-area');
                            mainOutputArea.innerHTML = `<div id="stats-dashboard-container" class="stats-dashboard-container" style="display: block;"><h3>لوحة المعلومات الإحصائية</h3><div id="stats-dashboard" class="stats-dashboard"><div class="stat-card"><h4><span class="stat-icon">📊</span> إحصائيات عامة</h4><ul id="general-stats-list"></ul></div><div class="stat-card"><h4><span class="stat-icon">📚</span> عدد المواد لكل مستوى</h4><ul id="level-counts-list"></ul></div><div class="stat-card"><h4><span class="stat-icon">✅</span> عدد المواد الموزعة لكل مستوى</h4><ul id="placed-level-counts-list"></ul></div><div class="stat-card"><h4><span class="stat-icon">📈</span> الأساتذة الأكثر عبئاً</h4><ul id="most-burdened-list"></ul></div><div class="stat-card"><h4><span class="stat-icon">📉</span> الأساتذة الأقل عبئاً</h4><ul id="least-burdened-list"></ul></div><div class="stat-card"><h4><span class="stat-icon">⚠️</span> حالات الفشل</h4><table id="failure-stats-list" class="mini-failure-table"></table></div></div></div><div id="schedules-display"></div>`;
                            populateDashboard(data);
                            displaySchedules(data.schedule, data.days, data.slots);
                            displayFailureReport(data.failures, data.unassigned_courses);
                        } finally {
                            resetGenerationUI();
                            let finalMessage = "اكتملت عملية الجدولة.\n\n" + (data.failures && data.failures.length > 0 ? `--- تقرير الفشل (${data.failures.length} حالة) ---\n` + data.failures.slice(0, 5).map(f => `• ${f.teacher_name || "N/A"}: ${f.reason || "N/A"}`).join('\n') : "تم إنشاء الجداول بنجاح.");
                            alert(finalMessage);
                        }
                        
                        eventSource.close();
                    } else if (message.startsWith("PROGRESS:")) {
                        const percentage = parseFloat(message.substring(9));
                        if (progressBarContainer.style.display === 'none') progressBarContainer.style.display = 'block';
                        progressBar.style.width = percentage + '%';
                        progressBar.textContent = percentage.toFixed(1) + '%';
                        if (percentage < 30) progressBar.style.backgroundColor = '#dc3545';
                        else if (percentage < 70) progressBar.style.backgroundColor = '#fd7e14';
                        else progressBar.style.backgroundColor = '#198754';
                    } else {
                        logOutput.textContent += message + '\n';
                        logOutput.scrollTop = logOutput.scrollHeight;
                    }
                };

                eventSource.onerror = function() {
                    logOutput.textContent += '\n--- انقطع الاتصال بالخادم. ---\n';
                    resetGenerationUI();
                    eventSource.close();
                };
            } else {
                throw new Error(data.error || 'خطأ غير معروف في الخادم');
            }
        })
        .catch(err => {
            console.error('Error starting schedule generation:', err);
            logOutput.textContent += `\nفشل الاتصال بالخادم أو بدء العملية: ${err.message}`;
            resetGenerationUI();
        });
    });

    stopBtn.addEventListener('click', function() {
        if(confirm('هل أنت متأكد من أنك تريد إيقاف عملية البحث؟')) {
            fetch('/api/stop-generation', { method: 'POST' });
            this.disabled = true;
            this.textContent = '...جاري الإيقاف';
            if (eventSource) {
                eventSource.close();
            }
        }
    });

    // --- بقية المستمعات الأخرى ---
    document.querySelectorAll('input[name="scheduling_method"]').forEach(radio => {
        radio.addEventListener('change', (event) => {
            document.getElementById('timeout-container').style.display = 'none';
            document.getElementById('tabu-search-container').style.display = 'none';
            document.getElementById('genetic-algorithm-container').style.display = 'none';
            document.getElementById('lns-container').style.display = 'none';
            document.getElementById('vns-container').style.display = 'none';
            document.getElementById('memetic-algorithm-container').style.display = 'none';
            document.getElementById('clonalg-container').style.display = 'none';
            document.getElementById('hyper-heuristic-container').style.display = 'none';

            if (event.target.value === 'backtracking') document.getElementById('timeout-container').style.display = 'block';
            else if (event.target.value === 'tabu_search') document.getElementById('tabu-search-container').style.display = 'block';
            else if (event.target.value === 'genetic_algorithm') document.getElementById('genetic-algorithm-container').style.display = 'block';
            else if (event.target.value === 'large_neighborhood_search') document.getElementById('lns-container').style.display = 'block';
            else if (event.target.value === 'variable_neighborhood_search' || event.target.value === 'vns_flexible') document.getElementById('vns-container').style.display = 'block';
            else if (event.target.value === 'memetic_algorithm') document.getElementById('memetic-algorithm-container').style.display = 'block';
            else if (event.target.value === 'clonalg') document.getElementById('clonalg-container').style.display = 'block';
            else if (event.target.value === 'hyper_heuristic') document.getElementById('hyper-heuristic-container').style.display = 'block';
        });
    });
    
    // ... (الكود المتبقي من الدالة الأصلية يبقى كما هو)
    document.getElementById('save-identifiers-btn').addEventListener('click', () => {
        const identifiersToSave = {};
        document.querySelectorAll('#identifiers-table-container textarea').forEach(textarea => {
            const level = textarea.dataset.level;
            const identifiers = textarea.value.split('\n').map(id => id.trim()).filter(Boolean);
            identifiersToSave[level] = identifiers;
        });

        fetch('/api/identifiers', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(identifiersToSave)
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) alert(data.message);
            else alert('حدث خطأ أثناء الحفظ.');
        });
    });

    document.getElementById('teacher-search').addEventListener('input', function(e) {
        const searchTerm = e.target.value.toLowerCase().trim();
        document.querySelectorAll('#teacher-list li').forEach(item => {
            item.style.display = item.textContent.toLowerCase().includes(searchTerm) ? '' : 'none';
        });
    });

    document.getElementById('course-search').addEventListener('input', function(e) {
        const searchTerm = e.target.value.toLowerCase().trim();
        document.querySelectorAll('#course-list li').forEach(item => {
            item.style.display = item.textContent.toLowerCase().includes(searchTerm) ? 'list-item' : 'none';
        });
    });

    document.getElementById('add-teacher-form').addEventListener('submit', function(event) {
        event.preventDefault();
        const teacherNamesInput = document.getElementById('teacher-names');
        const teacherNames = teacherNamesInput.value.split('\n').map(name => name.trim()).filter(Boolean);
        if (teacherNames.length === 0) return;
        fetch('/api/teachers', { 
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' }, 
            body: JSON.stringify({ names: teacherNames })
        })
        .then(response => { if (response.ok) { teacherNamesInput.value = ''; loadInitialData(); } else { alert('فشل في إضافة الأساتذة.'); }});
    });

    document.getElementById('add-room-form').addEventListener('submit', function(event) {
        event.preventDefault();
        const roomNamesInput = document.getElementById('room-names');
        const roomTypeSelect = document.getElementById('room-type');
        const roomNames = roomNamesInput.value.split('\n').map(name => name.trim()).filter(Boolean);
        const roomType = roomTypeSelect.value;
        if (roomNames.length === 0 || !roomType) return;
        fetch('/api/rooms', { 
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' }, 
            body: JSON.stringify({ names: roomNames, type: roomType })
        })
        .then(response => { if (response.ok) { roomNamesInput.value = ''; roomTypeSelect.selectedIndex = 0; loadInitialData(); } else { alert('فشل في إضافة القاعات.'); }});
    });

    document.getElementById('add-levels-form').addEventListener('submit', function(event) {
        event.preventDefault();
        const levelNamesInput = document.getElementById('level-names');
        const levelNames = levelNamesInput.value.split('\n').map(name => name.trim()).filter(Boolean);
        if (levelNames.length === 0) return;
        fetch('/api/levels', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ levels: levelNames })
        })
        .then(response => { if (response.ok) { levelNamesInput.value = ''; loadInitialData(); } else { alert('فشل في إضافة المستويات.'); }});
    });

    document.getElementById('bulk-add-form').addEventListener('submit', function(event) {
        event.preventDefault();
        const selectedLevels = Array.from(document.querySelectorAll('#bulk-course-levels-container input:checked')).map(cb => cb.value);
        const roomType = document.getElementById('bulk-room-type').value;
        const rawText = document.getElementById('course-names-bulk').value;
        if (selectedLevels.length === 0) { alert('الرجاء اختيار مستوى دراسي واحد على الأقل.'); return; }
        const courseNames = rawText.split('\n').map(line => line.trim()).filter(line => line.length > 0);
        if (courseNames.length === 0) { alert('الرجاء لصق اسم مقرر واحد على الأقل.'); return; }
        const courses = courseNames.map(name => ({ name: name, levels: selectedLevels, room_type: roomType }));
        fetch('/api/students/bulk', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(courses) })
        .then(response => response.json().then(data => { if (!response.ok) throw new Error(data.error || 'Server error'); return data; }))
        .then(data => { alert(data.message); document.getElementById('course-names-bulk').value = ''; loadInitialData(); })
        .catch(error => { alert('Error: ' + error.message); console.error('Error Details:', error); });
    });

    document.getElementById('teacher-list').addEventListener('click', function(event) {
        const target = event.target;
        if (target.classList.contains('courses-dropdown-btn')) {
            event.stopPropagation();
            const dropdown = target.closest('.teacher-entry').nextElementSibling;
            document.querySelectorAll('.courses-dropdown-list').forEach(d => { if (d !== dropdown) d.style.display = 'none'; });
            if (dropdown) { dropdown.style.display = dropdown.style.display === 'block' ? 'none' : 'block'; }
            return;
        }
        const li = target.closest('li');
        if (li) {
            document.querySelectorAll('#teacher-list .selected').forEach(el => el.classList.remove('selected'));
            li.classList.add('selected');
            selectedTeacher = li.querySelector('.teacher-name').firstChild.textContent.trim();
            updateAssignButtonState();
        }
    });

    document.getElementById('course-list').addEventListener('click', function(event) {
        if (event.target && event.target.nodeName === 'LI') {
            if (!event.target.classList.contains('assigned')) {
                event.target.classList.toggle('selected');
                updateAssignButtonState();
            } else {
                if (confirm(`هل أنت متأكد من أنك تريد إلغاء تخصيص المقرر "${event.target.textContent}"؟`)) {
                    fetch('/api/unassign-course', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ course_id: parseInt(event.target.dataset.courseId) }) })
                    .then(response => response.json())
                    .then(data => { if (data.success) { alert(data.message); loadInitialData(); } else { alert('Error: ' + data.error); }})
                    .catch(error => console.error('Error:', error));
                }
            }
        }
    });

    document.getElementById('assign-button').addEventListener('click', function() {
        const selectedCourseElements = document.querySelectorAll('#course-list .selected');
        if (selectedTeacher && selectedCourseElements.length > 0) {
            const selectedCourseIds = Array.from(selectedCourseElements).map(li => parseInt(li.dataset.courseId));
            fetch('/api/assign-courses/bulk', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ teacher: selectedTeacher, course_ids: selectedCourseIds }) })
            .then(response => response.json())
            .then(result => {
                if (result.success) {
                    alert(result.message);
                    loadInitialData();
                    selectedTeacher = null;
                    document.querySelectorAll('#teacher-list .selected').forEach(li => li.classList.remove('selected'));
                    updateAssignButtonState();
                } else { alert('Error: ' + result.error); }
            });
        }
    });
    
    document.getElementById('validate-data-button').addEventListener('click', function() {
        if (!currentScheduleData.schedule) {
            alert('يجب إنشاء جدول أولاً قبل فحصه.');
            return;
        }
        const manual_days = {};
        document.querySelectorAll('#professor-days-table-container input[data-day]:checked').forEach(cb => {
            const teacher = cb.dataset.teacher;
            const day = cb.dataset.day;
            if (!manual_days[teacher]) manual_days[teacher] = [];
            manual_days[teacher].push(day);
        });
        const special_constraints = {};
        document.querySelectorAll('#professor-days-table-container input[data-constraint]:checked').forEach(cb => {
            const teacher = cb.dataset.teacher;
            const constraint = cb.dataset.constraint;
            if (!special_constraints[teacher]) special_constraints[teacher] = {};
            special_constraints[teacher][constraint] = true;
        });
        document.querySelectorAll('#professor-days-table-container select[data-rule-type="distribution"]').forEach(select => {
            const teacher = select.dataset.teacher;
            if (!special_constraints[teacher]) special_constraints[teacher] = {};
            special_constraints[teacher]['distribution_rule'] = select.value;
        });
        const settingsForValidation = {
            schedule_structure: collectScheduleStructureFromUI(),
            manual_days: manual_days,
            special_constraints: special_constraints,
            distribution_rule_type: document.querySelector('input[name="distribution_rule_type"]:checked').value,
            saturday_teachers: Array.from(document.querySelectorAll('#saturday-teachers-checkbox-container input:checked')).map(cb => cb.value)
        };
        const payload = { schedule: currentScheduleData.schedule, days: currentScheduleData.days, slots: currentScheduleData.slots, settings: settingsForValidation };
        this.textContent = 'جاري الفحص...';
        this.disabled = true;
        fetch('/api/validate-schedule', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
        .then(response => response.json())
        .then(conflicts => {
        if (conflicts.length > 0) {
            const formattedConflicts = conflicts.map(conflict => {
                const teacher = conflict.teacher_name || "N/A";
                const course = conflict.course_name || "N/A";
                const reason = conflict.reason || "سبب غير معروف";
                return `الأستاذ: ${teacher}\n  - المادة/القيد: ${course}\n  - السبب: ${reason}`;
            });
            alert("تم العثور على التعارضات التالية في الجدول:\n\n" + formattedConflicts.join('\n\n-----------------\n'));
        } else {
            alert('فحص الجدول مكتمل: لم يتم العثور على أي تعارضات.');
        }
        })
        .catch(error => { console.error('Error during validation:', error); alert('حدث خطأ أثناء فحص الجدول.'); })
        .finally(() => { this.textContent = 'فحص البيانات بحثاً عن مشاكل'; this.disabled = false; });
    });

    document.getElementById('add-specific-room-assignment-btn').addEventListener('click', () => {
        const container = document.getElementById('specific-small-room-assignments-container');
        addSpecificRoomAssignmentRow(container);
    });
    
    document.getElementById('comprehensive-check-btn').addEventListener('click', async () => {
        if (!currentScheduleData.schedule) {
            alert('يجب إنشاء جدول أولاً قبل فحصه بشكل شامل.');
            return;
        }
        const reportSection = document.getElementById('comprehensive-check-report-section');
        const resultsContainer = document.getElementById('comprehensive-check-results-container');
        reportSection.style.display = 'block';
        resultsContainer.innerHTML = '<p><i class="fas fa-spinner fa-spin"></i> جاري الفحص الشامل...</p>';

        try {
            const currentSettings = collectAllCurrentSettings();
            const response = await fetch('/api/comprehensive-check', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ schedule: currentScheduleData.schedule, settings: currentSettings })
            });
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || 'حدث خطأ في الخادم.');
            }
            const findings = await response.json();
            if (findings.length === 0) {
                resultsContainer.innerHTML = '<p class="text-success" style="font-weight: bold;">✅ فحص شامل: لم يتم العثور على مواد ناقصة أو حالات تكرار للأساتذة/القاعات.</p>';
            } else {
                const table = document.createElement('table');
                table.className = 'management-table';
                table.innerHTML = `<thead><tr><th>نوع المشكلة</th><th>المادة</th><th>الأستاذ</th><th>السبب/الملاحظة</th></tr></thead><tbody></tbody>`;
                const tbody = table.querySelector('tbody');
                findings.forEach(finding => {
                    const row = tbody.insertRow();
                    let typeCell;
                    switch(finding.type) {
                        case 'missing': typeCell = '<strong style="color: #dc3545;">🔴 مادة ناقصة</strong>'; break;
                        case 'duplicate_teacher': typeCell = '<strong style="color: #ffc107;">🟡 أستاذ مكرر</strong>'; break;
                        case 'duplicate_room': typeCell = '<strong style="color: #ffc107;">🟡 قاعة مكررة</strong>'; break;
                        default: typeCell = 'ملاحظة';
                    }
                    row.insertCell().innerHTML = typeCell;
                    row.insertCell().textContent = finding.course_name || 'N/A';
                    row.insertCell().textContent = finding.teacher_name || 'N/A';
                    row.insertCell().textContent = finding.reason || 'N/A';
                });
                resultsContainer.innerHTML = '';
                resultsContainer.appendChild(table);
            }
        } catch (error) {
            console.error('Error during comprehensive check:', error);
            resultsContainer.innerHTML = `<p style="color: red;">فشل الفحص الشامل: ${error.message}</p>`;
        }
    });

    const duplicateDayBtn = document.getElementById('duplicate-day-button');
    if (duplicateDayBtn) {
        duplicateDayBtn.addEventListener('click', () => {
            const activeDayPane = document.querySelector('.tab-content .tab-pane.active');
            if (activeDayPane) {
                duplicateDay(activeDayPane);
            } else {
                alert('الرجاء اختيار يوم أولاً لتكراره.');
            }
        });
    }
}

// ==================== دوال إدارة الفئات المرنة ====================
function setupFlexCategoriesListeners() {
    const levelSelect = document.getElementById('flex-category-level-select');
    const addBtn = document.getElementById('add-flex-category-btn');
    const container = document.getElementById('flex-categories-container');

    if (!levelSelect || !addBtn || !container) return;

    levelSelect.addEventListener('change', () => {
        addBtn.disabled = !levelSelect.value;
    });

    addBtn.addEventListener('click', () => {
        const selectedLevel = levelSelect.value;
        if (selectedLevel) {
            createFlexCategoryUI({ level: selectedLevel, name: `فئة جديدة`, professors: [], courses: [] });
        }
    });
}

function createFlexCategoryUI(categoryData) {
    const container = document.getElementById('flex-categories-container');
    const categoryId = `flex-cat-${Date.now()}`;
    
    const categoryDiv = document.createElement('div');
    categoryDiv.className = 'flex-category';
    categoryDiv.dataset.categoryId = categoryId;
    categoryDiv.dataset.level = categoryData.level;

    categoryDiv.innerHTML = `
        <div class="flex-category-header">
            <input type="text" value="${categoryData.name}" placeholder="اسم الفئة (مثال: فئة البلاغة)">
            <button class="remove-flex-category-btn">حذف الفئة</button>
        </div>
        <div class="flex-category-body">
            <div class="flex-professors">
                <strong>الأساتذة والحصص:</strong>
                <div class="professors-list"></div>
                <button class="add-professor-to-category-btn" type="button" style="width: auto; padding: 5px 10px; margin-top: 10px;">+ أستاذ</button>
            </div>
            <div class="flex-courses">
                <strong>المواد (الأفواج) في هذه الفئة:</strong>
                <textarea placeholder="ضع كل اسم مادة في سطر منفصل...">${categoryData.courses.join('\n')}</textarea>
            </div>
        </div>
    `;

    container.appendChild(categoryDiv);

    // ربط الأحداث
    categoryDiv.querySelector('.remove-flex-category-btn').addEventListener('click', () => categoryDiv.remove());
    categoryDiv.querySelector('.add-professor-to-category-btn').addEventListener('click', (e) => {
        const list = e.target.previousElementSibling;
        addProfessorQuotaRow(list);
    });

    // ملء بيانات الأساتذة المحفوظة
    const professorsListDiv = categoryDiv.querySelector('.professors-list');
    if (categoryData.professors && categoryData.professors.length > 0) {
        categoryData.professors.forEach(prof => addProfessorQuotaRow(professorsListDiv, prof.name, prof.quota));
    } else {
        addProfessorQuotaRow(professorsListDiv); // إضافة صف فارغ واحد على الأقل
    }
}

function addProfessorQuotaRow(container, selectedTeacher = '', quota = 1) {
    const row = document.createElement('div');
    row.className = 'professor-quota-row';

    const select = document.createElement('select');
    select.innerHTML = '<option value="">-- اختر أستاذ --</option>';
    allAvailableTeachers.forEach(teacher => {
        select.add(new Option(teacher.name, teacher.name));
    });
    if (selectedTeacher) {
        select.value = selectedTeacher;
    }

    const input = document.createElement('input');
    input.type = 'number';
    input.min = 1;
    input.value = quota;
    input.title = "عدد الحصص";

    const removeBtn = document.createElement('button');
    removeBtn.textContent = '×';
    removeBtn.type = 'button';
    removeBtn.style.width = 'auto';
    removeBtn.style.padding = '2px 6px';
    removeBtn.onclick = () => row.remove();
    
    row.append(select, input, removeBtn);
    container.appendChild(row);
}

function resetGenerationUI() {
    const generateBtn = document.getElementById('generate-schedule-button');
    const stopBtn = document.getElementById('stop-generation-button');
    
    stopBtn.style.display = 'none';
    generateBtn.style.display = 'inline-block';
    document.getElementById('comprehensive-check-btn').style.display = 'inline-block';
}

function handleBulkExport(url, scheduleData, button, originalText) {
    button.textContent = 'جاري التصدير...';
    button.disabled = true;

    fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(scheduleData)
    })
    .then(response => {
        if (!response.ok) {
            throw new Error('فشل الخادم في إنشاء الملف.');
        }
        
        // ================== بداية الجزء المُعدل ==================
        // شيفرة جديدة ومحسنة لاستخراج اسم الملف بشكل صحيح
        const header = response.headers.get('Content-Disposition');
        let filename = 'جدول.xlsx'; // اسم افتراضي في حالة عدم العثور على اسم
        if (header) {
            // RFC 6266 (يبحث عن الصيغة الحديثة التي تدعم كل الحروف)
            const filenameStarMatch = /filename\*=UTF-8''([^;]+)/.exec(header);
            if (filenameStarMatch && filenameStarMatch[1]) {
                filename = decodeURIComponent(filenameStarMatch[1]);
            } else {
                // RFC 2616 (إذا لم يجد الصيغة الحديثة، يبحث عن الصيغة القديمة)
                const filenameMatch = /filename="([^"]+)"/.exec(header);
                if (filenameMatch && filenameMatch[1]) {
                    filename = filenameMatch[1];
                }
            }
        }
        // ================== نهاية الجزء المُعدل ==================

        return Promise.all([response.blob(), filename]);
    })
    .then(([blob, filename]) => {
        const downloadUrl = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = downloadUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(downloadUrl);
        document.body.removeChild(a);
        button.disabled = false;
        button.textContent = originalText;
    })
    .catch(error => {
        console.error('Export Error:', error);
        alert('حدث خطأ أثناء عملية التصدير.');
        button.disabled = false;
        button.textContent = originalText;
    });
}

function updateAssignButtonState() {
    const assignButton = document.getElementById('assign-button');
    if (!assignButton) return;
    const selectedCoursesCount = document.querySelectorAll('#course-list li.selected').length;
    assignButton.disabled = !(selectedTeacher && selectedCoursesCount > 0);
}

function populateManagementLists(teachers, rooms, courses, levels) {
    const process = (listId, data, name_key, levels_key, type_key) => {
        const listElement = document.getElementById(listId);
        if (!listElement) return;
        listElement.innerHTML = '';

        data.forEach(item => {
            const li = document.createElement('li');
            
            // ✨ تخزين الـ ID وبيانات أخرى في العنصر مباشرة
            if (item.id) {
                li.dataset.itemId = item.id;
            }

            let displayName = '';
            
            if (listId === 'manage-courses-list') {
                // ✨ عرض كل المستويات مفصولة بفاصلة
                const levelsText = (item[levels_key] && item[levels_key].length > 0) ? item[levels_key].join('، ') : 'غير محدد';
                 displayName = `${item[name_key]} (${levelsText})`;
            } else if (type_key) {
                displayName = `${item[name_key]} (${item[type_key]})`;
            } else {
                displayName = item[name_key] || item;
            }

            li.innerHTML = `
                <span class="item-name-display">${displayName}</span>
                <div class="item-actions">
                    <button class="edit-btn" title="تعديل">📝</button>
                    <button class="delete-btn" title="حذف">&times;</button>
                </div>`;

            li.querySelector('.delete-btn').addEventListener('click', () => {
                if (!confirm(`هل أنت متأكد من أنك تريد حذف "${displayName}"؟`)) return;

                let endpoint = '';
                let body = {};
                const name = item[name_key] || item;
                const itemId = li.dataset.itemId;

                switch (listId) {
                    case 'manage-teachers-list': endpoint = '/api/teachers'; body = { name }; break;
                    case 'manage-rooms-list': endpoint = '/api/rooms'; body = { name }; break;
                    case 'manage-levels-list': endpoint = '/api/levels'; body = { level: name }; break;
                    // ✨ تعديل الحذف ليعتمد على الـ ID
                    case 'manage-courses-list': endpoint = '/api/students'; body = { id: parseInt(itemId) }; break;
                }
                
                fetch(endpoint, { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
                .then(response => response.ok ? response.json() : response.json().then(err => Promise.reject(err)))
                .then(data => { alert(data.message || 'تم الحذف بنجاح'); loadInitialData(); })
                .catch(err => alert(`خطأ: ${err.error || 'فشل الحذف'}`));
            });

            li.querySelector('.edit-btn').addEventListener('click', () => {
                // ✨ استبدال منطق التعديل القديم بمنطق جديد أكثر تطوراً
                if (listId === 'manage-courses-list') {
                    renderCourseEditUI(li, item, levels);
                } else {
                    // المنطق القديم لبقية العناصر يبقى كما هو
                    const name = item[name_key] || item;
                    switch (listId) {
                        case 'manage-teachers-list': {
                            const newName = prompt('أدخل الاسم الجديد للأستاذ:', name);
                            if (newName && newName.trim()) {
                                fetch('/api/teachers/edit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ old_name: name, new_name: newName.trim() }) })
                                .then(res => res.json()).then(data => { if(data.success) loadInitialData(); else alert('خطأ: ' + data.error); });
                            }
                            break;
                        }
                        case 'manage-rooms-list': {
                            const newName = prompt('أدخل الاسم الجديد للقاعة:', name);
                            const newType = prompt('أدخل النوع الجديد للقاعة (كبيرة/صغيرة):', item[type_key]);
                            if (newName && newName.trim() && newType && ['كبيرة', 'صغيرة'].includes(newType.trim())) {
                                 fetch('/api/rooms/edit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ old_name: name, new_name: newName.trim(), new_type: newType.trim() }) })
                                .then(res => res.json()).then(data => { if(data.success) loadInitialData(); else alert('خطأ: ' + data.error); });
                            }
                            break;
                        }
                        case 'manage-levels-list': {
                            const newLevelName = prompt('أدخل الاسم الجديد للمستوى:', name);
                            if (newLevelName && newLevelName.trim()) {
                                fetch('/api/levels/edit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ old_level: name, new_level: newLevelName.trim() }) })
                                .then(res => res.json()).then(data => { if(data.success) loadInitialData(); else alert('خطأ: ' + data.error); });
                            }
                            break;
                        }
                    }
                }
            });
            listElement.appendChild(li);
        });
    };
    
    process('manage-teachers-list', teachers, 'name');
    process('manage-rooms-list', rooms, 'name', null, 'type');
    process('manage-levels-list', levels);
    process('manage-courses-list', courses, 'name', 'levels', 'room_type');
}

// ✨✨✨ إضافة دالة جديدة بالكامل في ملف script.js ✨✨✨
// يمكنك وضع هذه الدالة في أي مكان في الملف، مثلاً بعد دالة populateManagementLists مباشرة.
function renderCourseEditUI(listItem, courseData, allLevels) {
    // إخفاء الواجهة العادية
    const nameSpan = listItem.querySelector('.item-name-display');
    const actionsDiv = listItem.querySelector('.item-actions');
    nameSpan.style.display = 'none';
    actionsDiv.style.display = 'none';

    // إنشاء واجهة التعديل
    const editContainer = document.createElement('div');
    editContainer.className = 'edit-course-container';
    editContainer.style.width = '100%';

    // حقل لاسم المقرر
    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.value = courseData.name;
    nameInput.style.width = '95%';
    nameInput.style.marginBottom = '10px';
    
    // حقل لنوع القاعة
    const roomTypeSelect = document.createElement('select');
    roomTypeSelect.innerHTML = `
        <option value="صغيرة" ${courseData.room_type === 'صغيرة' ? 'selected' : ''}>صغيرة</option>
        <option value="كبيرة" ${courseData.room_type === 'كبيرة' ? 'selected' : ''}>كبيرة</option>
    `;
    roomTypeSelect.style.marginBottom = '10px';

    // حاوية لمربعات اختيار المستويات
    const levelsCheckboxes = document.createElement('div');
    levelsCheckboxes.style.border = '1px solid #ccc';
    levelsCheckboxes.style.padding = '8px';
    levelsCheckboxes.style.maxHeight = '100px';
    levelsCheckboxes.style.overflowY = 'auto';
    allLevels.forEach(level => {
        const isChecked = courseData.levels.includes(level) ? 'checked' : '';
        levelsCheckboxes.innerHTML += `<label style="display: block;"><input type="checkbox" value="${level}" ${isChecked}> ${level}</label>`;
    });

    // أزرار الحفظ والإلغاء
    const saveButton = document.createElement('button');
    saveButton.textContent = 'حفظ';
    saveButton.style.width = 'auto';
    saveButton.style.padding = '5px 10px';
    saveButton.style.margin = '10px 0 0 5px';
    
    const cancelButton = document.createElement('button');
    cancelButton.textContent = 'إلغاء';
    cancelButton.style.backgroundColor = '#6c757d';
    cancelButton.style.width = 'auto';
    cancelButton.style.padding = '5px 10px';

    // إضافة كل العناصر للحاوية
    editContainer.append(nameInput, roomTypeSelect, levelsCheckboxes, saveButton, cancelButton);
    listItem.appendChild(editContainer);

    // منطق الإلغاء
    cancelButton.addEventListener('click', () => {
        editContainer.remove();
        nameSpan.style.display = 'inline';
        actionsDiv.style.display = 'flex';
    });

    // منطق الحفظ
    saveButton.addEventListener('click', () => {
        const newName = nameInput.value.trim();
        const newRoomType = roomTypeSelect.value;
        const newLevels = Array.from(levelsCheckboxes.querySelectorAll('input:checked')).map(cb => cb.value);

        if (!newName || newLevels.length === 0) {
            alert('يجب إدخال اسم للمقرر واختيار مستوى واحد على الأقل.');
            return;
        }

        const payload = {
            id: courseData.id,
            new_name: newName,
            new_room_type: newRoomType,
            new_levels: newLevels
        };

        fetch('/api/students/edit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                loadInitialData(); // إعادة تحميل الكل لتحديث الواجهة
            } else {
                alert('خطأ: ' + data.error);
                // في حالة الخطأ، نعيد الواجهة لوضعها الأصلي
                editContainer.remove();
                nameSpan.style.display = 'inline';
                actionsDiv.style.display = 'flex';
            }
        });
    });
}

// استبدل الدالة القديمة بالكامل بهذه النسخة
function loadAndBuildIdentifiersTable() {
    Promise.all([
        fetch('/api/levels').then(res => res.json()),
        fetch('/api/identifiers').then(res => res.json())
    ]).then(([levels, savedIdentifiers]) => {
        const container = document.getElementById('identifiers-table-container');
        if (!container) return;
        container.innerHTML = '';

        if (levels.length === 0) {
            container.innerHTML = '<p>الرجاء إضافة مستويات دراسية أولاً.</p>';
            return;
        }

        const chunkSize = 5; // الحد الأقصى للأعمدة

        // المرور على المستويات في مجموعات
        for (let i = 0; i < levels.length; i += chunkSize) {
            const levelChunk = levels.slice(i, i + chunkSize);

            // --- بداية التعديل الرئيسي ---
            // 1. إنشاء جدول جديد ومستقل **داخل الحلقة** لكل مجموعة
            const table = document.createElement('table');
            table.className = 'schedule-table';
            table.style.width = '100%';
            table.style.tableLayout = 'fixed'; // الآن ستعمل بشكل صحيح
            table.style.marginBottom = '25px'; // (اختياري) لإضافة مسافة بين الجداول

            const tbody = table.createTBody();
            // --- نهاية التعديل الرئيسي ---


            // 2. إنشاء صف العناوين (أسماء المستويات) لهذه المجموعة
            const headerChunkRow = tbody.insertRow();
            levelChunk.forEach(level => {
                const th = document.createElement('th');
                th.textContent = level;
                headerChunkRow.appendChild(th);
            });

            // 3. إنشاء صف حقول الإدخال (textarea) لهذه المجموعة
            const textareaChunkRow = tbody.insertRow();
            levelChunk.forEach(level => {
                const cell = textareaChunkRow.insertCell();
                cell.style.height = 'auto';
                const identifiersText = (savedIdentifiers[level] || []).join('\n');
                cell.innerHTML = `<textarea data-level="${level}" style="width: 100%; height: 150px; resize: vertical; padding: 8px; font-size: 16px; border: 1px solid #ccc; border-radius: 4px;">${identifiersText}</textarea>`;
            });

            // 4. إضافة الجدول المكتمل لهذه المجموعة إلى الحاوية الرئيسية
            container.appendChild(table);
        }

    }).catch(error => console.error('خطأ في بناء جدول المعرفات:', error));
}

function displaySchedules(scheduleData, days, slots) {
    const outputDiv = document.getElementById('schedules-display');
    if (!outputDiv) { console.error("Error: schedules-display container not found!"); return; }
    outputDiv.innerHTML = ''; 

    if (!scheduleData || Object.keys(scheduleData).length === 0) {
        outputDiv.innerHTML = '<h3>لم يتم إنشاء أي جداول.</h3>';
        return;
    }

    const buttonsContainer = document.createElement('div');
    buttonsContainer.className = 'export-buttons-container';

    const exportLevelsBtn = document.createElement('button');
    exportLevelsBtn.textContent = 'تصدير جداول المستويات (Excel)';
    exportLevelsBtn.addEventListener('click', () => {
        if (!currentScheduleData.schedule) { alert('بيانات المستويات غير جاهزة.'); return; }
        const dataToExport = { schedule: currentScheduleData.schedule, days: days, slots: slots };
        handleBulkExport('/api/export/all-levels', dataToExport, exportLevelsBtn, 'تصدير جداول المستويات (Excel)');
    });
    buttonsContainer.appendChild(exportLevelsBtn);

    const exportProfessorsBtn = document.createElement('button');
    exportProfessorsBtn.textContent = 'تصدير جداول الأساتذة (Excel)';
    exportProfessorsBtn.addEventListener('click', () => {
        if (!currentScheduleByProfessor) { alert('بيانات الأساتذة غير جاهزة.'); return; }
        const dataToExport = { schedule: currentScheduleByProfessor, days: days, slots: slots };
        handleBulkExport('/api/export/all-professors', dataToExport, exportProfessorsBtn, 'تصدير جداول الأساتذة (Excel)');
    });
    buttonsContainer.appendChild(exportProfessorsBtn);

    const toggleFreeRoomsBtn = document.createElement('button');
    toggleFreeRoomsBtn.textContent = 'إظهار القاعات الشاغرة';
    toggleFreeRoomsBtn.style.backgroundColor = '#6c757d';

    let freeRoomsVisible = false;

    toggleFreeRoomsBtn.addEventListener('click', () => {
        const container = document.getElementById('free-rooms-container');
        if (freeRoomsVisible && container) {
            container.remove();
            freeRoomsVisible = false;
            toggleFreeRoomsBtn.textContent = 'إظهار القاعات الشاغرة';
        } else {
            if (currentFreeRoomsSchedule) {
                 displayFreeRoomsSchedule(currentFreeRoomsSchedule, days, slots);
                 freeRoomsVisible = true;
                 toggleFreeRoomsBtn.textContent = 'إخفاء القاعات الشاغرة';
            }
        }
    });
    buttonsContainer.appendChild(toggleFreeRoomsBtn);
    
    outputDiv.appendChild(buttonsContainer);

    const levelNameMap = {"Bachelor 1": "ليسانس 1", "Bachelor 2": "ليسانس 2", "Bachelor 3": "ليسانس 3", "Master 1": "ماستر 1", "Master 2": "ماستر 2"};
    const sortedLevels = Object.keys(scheduleData).sort();
    for (const level of sortedLevels) {
        const grid = scheduleData[level];
        if (grid.length === 0) continue; 

        const container = document.createElement('div');
        container.className = 'schedule-container';
        const title = document.createElement('h3');
        title.className = 'schedule-title';
        title.textContent = levelNameMap[level] || level;
        container.appendChild(title);
        const table = document.createElement('table');
        table.className = 'schedule-table';
        const thead = table.createTHead();
        const headerRow = thead.insertRow();
        headerRow.innerHTML = '<th>الوقت</th>';
        days.forEach(day => headerRow.innerHTML += `<th>${day}</th>`);
        const tbody = table.createTBody();
        slots.forEach((slot, slotIdx) => {
            const row = tbody.insertRow();
            row.insertCell().innerHTML = `<strong>${slot}</strong>`;
            days.forEach((day, dayIdx) => {
                const cell = row.insertCell();
                const lecturesInCell = grid[dayIdx] ? grid[dayIdx][slotIdx] : [];
                if (lecturesInCell && lecturesInCell.length > 0) {
                    cell.innerHTML = lecturesInCell.map(lec => {
                        // ✨ نتحقق إذا كانت المادة ضمن قائمة المبدلين
                        const isSwapped = currentScheduleData.swapped_ids.includes(lec.id);
                        // ✨ نضيف كلاس 'swapped' إذا تحقق الشرط
                        return `
                            <div class="lecture-cell ${isSwapped ? 'swapped' : ''}">
                                <strong>${lec.name}</strong>
                                <span>${lec.teacher_name}</span>
                                <small>${lec.room}</small>
                            </div>
                        `;
                    }).join('<hr style="margin: 4px 0;">');
                }
            });
        });
        container.appendChild(table);
        outputDiv.appendChild(container);
    }
}


function displayFreeRoomsSchedule(freeRoomsData, days, slots) {
    const oldContainer = document.getElementById('free-rooms-container');
    if (oldContainer) oldContainer.remove();

    if (!freeRoomsData) return;

    const outputDiv = document.getElementById('schedules-display');
    const container = document.createElement('div');
    container.id = 'free-rooms-container';
    container.className = 'schedule-container';
    container.style.marginTop = '40px';

    const title = document.createElement('h3');
    title.className = 'schedule-title';
    title.textContent = 'جدول القاعات الشاغرة';
    container.appendChild(title);

    const exportBtnContainer = document.createElement('div');
    exportBtnContainer.style.textAlign = 'center';
    exportBtnContainer.style.padding = '10px';
    const exportFreeRoomsBtn = document.createElement('button');
    exportFreeRoomsBtn.textContent = 'تصدير القاعات الشاغرة (Excel)';
    exportFreeRoomsBtn.addEventListener('click', () => {
         const dataToExport = { schedule: freeRoomsData, days: days, slots: slots };
         handleBulkExport('/api/export/free-rooms', dataToExport, exportFreeRoomsBtn, 'تصدير القاعات الشاغرة (Excel)');
    });
    exportBtnContainer.appendChild(exportFreeRoomsBtn);
    container.appendChild(exportBtnContainer);


    const table = document.createElement('table');
    table.className = 'schedule-table';

    const thead = table.createTHead();
    const headerRow = thead.insertRow();
    headerRow.innerHTML = '<th>الوقت</th>';
    days.forEach(day => headerRow.innerHTML += `<th>${day}</th>`);
    const tbody = table.createTBody();
    slots.forEach((slot, slotIdx) => {
        const row = tbody.insertRow();
        row.insertCell().innerHTML = `<strong>${slot}</strong>`;
        days.forEach((day, dayIdx) => {
            const cell = row.insertCell();
            const freeRooms = freeRoomsData[dayIdx] ? freeRoomsData[dayIdx][slotIdx] : [];
            if (freeRooms.length > 0) {
                cell.innerHTML = freeRooms.join('<br>');
            } else {
                cell.innerHTML = '<i>-- لا يوجد --</i>';
            }
        });
    });

    container.appendChild(table);
    outputDiv.appendChild(container);
}

function applySettingsToUI(settings) {
    if (!settings) return;

    // إعادة ضبط جميع مربعات الاختيار في جدول الأساتذة قبل تطبيق الإعدادات الجديدة
    document.querySelectorAll('#professor-days-table-container input[type="checkbox"]').forEach(cb => cb.checked = false);
    document.querySelectorAll('#professor-days-table-container select[data-rule-type="distribution"]').forEach(select => select.value = 'غير محدد');
    document.querySelectorAll('#saturday-teachers-checkbox-container input[type="checkbox"]').forEach(cb => cb.checked = false);
    document.querySelectorAll('#no-last-two-slots-teachers-container input[type="checkbox"]').forEach(cb => cb.checked = false);


    // استعادة إعدادات المرحلة الخامسة (phase_5_settings الآن جزء من settings مباشرة)
    const p5s = settings.phase_5_settings || settings; // للتعامل مع الهيكل القديم والجديد
    if (p5s.rest_periods) {
        document.getElementById('tuesday-evening-rest').checked = p5s.rest_periods.tuesday_evening || false;
        document.getElementById('thursday-evening-rest').checked = p5s.rest_periods.thursday_evening || false;
    }
    
    if (p5s.special_constraints) {
        for (const teacher in p5s.special_constraints) {
            const constraints = p5s.special_constraints[teacher];
            for (const key in constraints) {
                if (key === 'distribution_rule') {
                    const select = document.querySelector(`select[data-teacher="${teacher}"][data-rule-type="distribution"]`);
                    if (select) select.value = constraints[key];
                } else {
                    const checkbox = document.querySelector(`input[data-teacher="${teacher}"][data-constraint="${key}"]`);
                    if (checkbox) checkbox.checked = true;
                }
            }
        }
    }

    if (p5s.manual_days) {
        for (const teacher in p5s.manual_days) {
            p5s.manual_days[teacher].forEach(day => {
                const checkbox = document.querySelector(`input[data-teacher="${teacher}"][data-day="${day}"]`);
                if (checkbox) checkbox.checked = true;
            });
        }
    }
    if (p5s.saturday_teachers) {
        p5s.saturday_teachers.forEach(teacherName => {
            const checkbox = document.querySelector(`#saturday-teachers-checkbox-container input[value="${teacherName}"]`);
            if(checkbox) checkbox.checked = true;
        });
    }

    if (p5s.last_slot_restrictions) {
        setTimeout(() => {
            for (const teacherName in p5s.last_slot_restrictions) {
                const restrictionValue = p5s.last_slot_restrictions[teacherName];
                const select = document.querySelector(`.last-slot-restriction-select[data-teacher-name="${teacherName}"]`);
                if (select) {
                    select.value = restrictionValue;
                }
            }
        }, 150);
    }

    if (p5s.level_specific_large_rooms) {
        for (const levelName in p5s.level_specific_large_rooms) {
            const roomName = p5s.level_specific_large_rooms[levelName];
            const select = document.querySelector(`#level-large-room-assignment-container select[data-level="${levelName}"]`);
            if (select) {
                select.value = roomName;
            }
        }
    }
    
    // === بداية التعديل الجوهري: استعادة هيكل الجدول بنظام الألسنة ===
    if (settings.schedule_structure) {
        const structure = settings.schedule_structure;
        // استهداف الحاويات الجديدة بدلاً من القديمة
        const tabsContainer = document.getElementById('day-tabs-container');
        const contentContainer = document.getElementById('day-content-container');
        
        // مسح الهيكل الحالي
        tabsContainer.innerHTML = ''; 
        contentContainer.innerHTML = '';

        const dayOrder = ['السبت', 'الأحد', 'الإثنين', 'الثلاثاء', 'الأربعاء', 'الخميس'];
        const sortedDays = Object.keys(structure).sort((a, b) => dayOrder.indexOf(a) - dayOrder.indexOf(b));
        
        for (const dayName of sortedDays) {
            addDayUI({ dayName: dayName, slots: structure[dayName] });
        }
    } else {
        // مسح الحاويات إذا لم توجد إعدادات محفوظة
        document.getElementById('day-tabs-container').innerHTML = '';
        document.getElementById('day-content-container').innerHTML = '';
    }
    // === نهاية التعديل الجوهري ===

    // استعادة إعدادات الخوارزميات
    const algoSettings = settings.algorithm_settings || settings;
    if(algoSettings.method) {
        const radio = document.querySelector(`input[name="scheduling_method"][value="${algoSettings.method}"]`);
        if(radio) {
            radio.checked = true;
            radio.dispatchEvent(new Event('change'));
        }
        if (algoSettings.hh_selected_llh) {
            document.querySelectorAll('input[name="hh_llh_select"]').forEach(cb => cb.checked = false);
            algoSettings.hh_selected_llh.forEach(heuristic_key => {
                const checkbox = document.querySelector(`input[name="hh_llh_select"][value="${heuristic_key}"]`);
                if (checkbox) checkbox.checked = true;
            });
        } else {
            document.querySelectorAll('input[name="hh_llh_select"]').forEach(cb => cb.checked = true);
        }
        document.getElementById('tabu-iterations-input').value = algoSettings.tabu_iterations || 1000;
        document.getElementById('tabu-tenure-input').value = algoSettings.tabu_tenure || 10;
        document.getElementById('tabu-neighborhood-size-input').value = algoSettings.tabu_neighborhood_size || 50;
        document.getElementById('ga-population-input').value = algoSettings.ga_population_size || 50;
        document.getElementById('ga-generations-input').value = algoSettings.ga_generations || 200;
        document.getElementById('ga-mutation-input').value = algoSettings.ga_mutation_rate || 5;
        document.getElementById('ga-elitism-input').value = algoSettings.ga_elitism_count || 2;
        document.getElementById('lns-iterations-input').value = algoSettings.lns_iterations || 500;
        document.getElementById('lns-ruin-factor-input').value = algoSettings.lns_ruin_factor || 20;
        document.getElementById('vns-iterations-input').value = algoSettings.vns_iterations || 300;
        document.getElementById('vns-k-max-input').value = algoSettings.vns_k_max || 10;
        document.getElementById('ma-population-input').value = algoSettings.ma_population_size || 40;
        document.getElementById('ma-generations-input').value = algoSettings.ma_generations || 100;
        document.getElementById('ma-mutation-input').value = algoSettings.ma_mutation_rate || 10;
        document.getElementById('ma-elitism-input').value = algoSettings.ma_elitism_count || 2;
        document.getElementById('ma-local-search-iterations').value = algoSettings.ma_local_search_iterations || 5;
        document.getElementById('clonalg-population-input').value = algoSettings.clonalg_population_size || 50;
        document.getElementById('clonalg-generations-input').value = algoSettings.clonalg_generations || 100;
        document.getElementById('clonalg-selection-input').value = algoSettings.clonalg_selection_size || 10;
        document.getElementById('clonalg-clone-factor-input').value = algoSettings.clonalg_clone_factor || 1.0;

        // --- استعادة إعدادات النظام الخبير ---
        document.getElementById('hh-iterations-input').value = algoSettings.hh_iterations || 50;
        if (algoSettings.hh_budget_mode) {
            const budgetRadio = document.querySelector(`input[name="hh_budget_mode"][value="${algoSettings.hh_budget_mode}"]`);
            if (budgetRadio) budgetRadio.checked = true;
        }
        document.getElementById('hh-time-budget-input').value = algoSettings.hh_time_budget || 5;
        document.getElementById('hh-llh-iterations-input').value = algoSettings.hh_llh_iterations || 30;
        document.getElementById('hh-tabu-tenure-input').value = algoSettings.hh_tabu_tenure || 3;
        if (algoSettings.hh_selected_llh) {
            document.querySelectorAll('input[name="hh_llh_select"]').forEach(cb => cb.checked = false);
            algoSettings.hh_selected_llh.forEach(llh_name => {
                const checkbox = document.querySelector(`input[name="hh_llh_select"][value="${llh_name}"]`);
                if (checkbox) checkbox.checked = true;
            });
        } else {
            document.querySelectorAll('input[name="hh_llh_select"]').forEach(cb => cb.checked = true);
        }
        document.getElementById('timeout-input').value = algoSettings.timeout || 30;
        document.getElementById('tabu-iterations-input').value = algoSettings.tabu_iterations || 1000;
        // ... (بقية حقول الخوارزميات تقع ضمن هذا النطاق ويجب أن تعمل بشكل صحيح) ...
        document.getElementById('intensive-search-attempts').value = algoSettings.intensive_search_attempts || 1;
        if (algoSettings.distribution_rule_type) {
            document.querySelector(`input[name="distribution_rule_type"][value="${algoSettings.distribution_rule_type}"]`).checked = true;
        }
        document.getElementById('prioritize-primary-slots-cb').checked = algoSettings.prioritize_primary || false;
        document.getElementById('teacher-pairs-textarea').value = algoSettings.teacher_pairs_text || '';
        document.getElementById('max-sessions-per-day-select').value = algoSettings.max_sessions_per_day || 'none';
        if (algoSettings.consecutive_large_hall_rule) {
            document.getElementById('consecutive-large-hall-select').value = algoSettings.consecutive_large_hall_rule;
        }
    }
    
    // استعادة الفئات المرنة
    if (settings.flexible_categories) {
        setTimeout(() => {
            const container = document.getElementById('flex-categories-container');
            if (container) {
                container.innerHTML = '';
                settings.flexible_categories.forEach(categoryData => {
                    createFlexCategoryUI(categoryData);
                });
            }
        }, 200);
    }
}

function addSpecificRoomAssignmentRow(container, assignment = {}) {
    const row = document.createElement('div');
    row.className = 'specific-room-assignment-row';
    row.style.display = 'flex';
    row.style.gap = '10px';
    row.style.marginBottom = '8px';
    row.style.alignItems = 'center';

    // القائمة المنسدلة للمقررات
    const courseSelect = document.createElement('select');
    courseSelect.className = 'course-select';
    courseSelect.style.flexGrow = '1';
    courseSelect.style.padding = '5px';

    // القائمة المنسدلة للقاعات الصغيرة
    const roomSelect = document.createElement('select');
    roomSelect.className = 'small-room-select';
    roomSelect.style.padding = '5px';

    const deleteBtn = document.createElement('button');
    deleteBtn.textContent = '×';
    deleteBtn.style.width = 'auto';
    deleteBtn.style.padding = '5px 10px';
    deleteBtn.style.backgroundColor = '#dc3545';
    deleteBtn.onclick = () => row.remove();

    row.append(courseSelect, roomSelect, deleteBtn);
    container.appendChild(row);

    // ملء القائمتين وتحديد القيم المحفوظة
    populateAssignmentDropdowns(courseSelect, roomSelect, assignment);
}

async function populateAssignmentDropdowns(courseSelect, roomSelect, assignment) {
    try {
        const coursesRes = await fetch('/students');
        const roomsRes = await fetch('/rooms');
        if (!coursesRes.ok || !roomsRes.ok) throw new Error('فشل في جلب البيانات');

        const allCourses = await coursesRes.json();
        const allRooms = await roomsRes.json();

        const smallRooms = allRooms.filter(r => r.type === 'صغيرة');

        courseSelect.innerHTML = '<option value="">-- اختر المقرر --</option>';
        allCourses.forEach(c => {
            const value = `${c.name} (${c.level})`;
            const option = new Option(value, value);
            courseSelect.appendChild(option);
        });

        roomSelect.innerHTML = '<option value="">-- اختر القاعة --</option>';
        smallRooms.forEach(r => {
            const option = new Option(r.name, r.name);
            roomSelect.appendChild(option);
        });

        if (assignment.course) {
            courseSelect.value = assignment.course;
        }
        if (assignment.room) {
            roomSelect.value = assignment.room;
        }
    } catch (error) {
        console.error("خطأ في ملء قوائم التخصيص:", error);
        courseSelect.innerHTML = '<option value="">خطأ في التحميل</option>';
        roomSelect.innerHTML = '<option value="">خطأ في التحميل</option>';
    }
}

function setupModalListeners() {
    const modal = document.getElementById("help-modal");
    const helpBtn = document.getElementById("help-button");
    const aboutBtn = document.getElementById("about-button");
    
    if (!modal || !helpBtn || !aboutBtn) {
        console.error("Modal or its control buttons not found.");
        return;
    }

    const closeBtn = modal.querySelector(".close-btn");

    helpBtn.addEventListener('click', () => modal.style.display = "block");
    aboutBtn.addEventListener('click', () => alert("Copyright (C) 2025 CHAIB YAHIA"));

    if (closeBtn) {
        closeBtn.addEventListener('click', () => modal.style.display = "none");
    }

    window.addEventListener('click', (event) => {
        if (event.target == modal) {
            modal.style.display = "none";
        }
    });
}


function addSlotUI(container, slotData = {}) {
    const slotDiv = document.createElement('div');
    slotDiv.className = 'time-slot-block';

    const [startTime, endTime] = slotData.time ? slotData.time.split('-') : ['08:00', '09:30'];

    const headerDiv = document.createElement('div');
    headerDiv.className = 'slot-header';
    headerDiv.innerHTML = `
        <div class="time-inputs">
            <input type="time" required value="${startTime}">
            <span>-</span>
            <input type="time" required value="${endTime}">
        </div>
        <div class="slot-actions">
            <button type="button" class="add-rule-btn">+ إضافة قيد</button>
            <button type="button" class="remove-slot-btn" title="حذف الفترة">&times;</button>
        </div>
    `;
    slotDiv.appendChild(headerDiv);

    const pinnedCourseSelect = document.createElement('select');
    pinnedCourseSelect.className = 'pinned-course-select';
    pinnedCourseSelect.style.width = '100%';
    pinnedCourseSelect.style.marginTop = '10px';
    pinnedCourseSelect.style.padding = '8px';
    pinnedCourseSelect.title = 'اختر مادة لتثبيتها في هذه الفترة الزمنية. سيتم تجاهلها إذا لم تكن مسندة لأستاذ.';

    let optionsHTML = '<option value="">-- تثبيت مادة (اختياري) --</option>';
    
    // === بداية التعديل: استخدام allAvailableCourses مباشرة ===
    if (allAvailableCourses && allAvailableCourses.length > 0) {
        allAvailableCourses.forEach(course => {
    // === نهاية التعديل ===
            const teacherName = course.teacher_name || 'غير مسند';
            const courseText = `${course.name} (${teacherName}) - [${(course.levels || []).join(',')}]`;
            const isSelected = slotData.pinnedCourseId === course.id ? 'selected' : '';
            optionsHTML += `<option value="${course.id}" ${isSelected}>${courseText}</option>`;
        });
    }
    pinnedCourseSelect.innerHTML = optionsHTML;
    slotDiv.appendChild(pinnedCourseSelect);

    const rulesContainer = document.createElement('div');
    rulesContainer.className = 'rules-container';
    slotDiv.appendChild(rulesContainer);

    headerDiv.querySelector('.remove-slot-btn').addEventListener('click', () => slotDiv.remove());
    headerDiv.querySelector('.add-rule-btn').addEventListener('click', () => addRuleUI(rulesContainer));

    if (slotData.rules && slotData.rules.length > 0) {
        slotData.rules.forEach(rule => addRuleUI(rulesContainer, rule));
    }

    container.appendChild(slotDiv);
    return slotDiv;
}


function addRuleUI(container, ruleData = {}) {
    const ruleDiv = document.createElement('div');
    ruleDiv.className = 'rule-block';

    const ruleType = ruleData.rule_type || 'ANY_HALL';
    const selectedLevels = ruleData.levels || [];
    const hallName = ruleData.hall_name || '';

    
    const ruleHeader = document.createElement('div');
    ruleHeader.className = 'rule-header';
    ruleHeader.innerHTML = `
        <select class="rule-type-select">
            <option value="ANY_HALL" ${ruleType === 'ANY_HALL' ? 'selected' : ''}>جميع القاعات متاحة</option>
            <option value="SMALL_HALLS_ONLY" ${ruleType === 'SMALL_HALLS_ONLY' ? 'selected' : ''}>القاعات الصغيرة فقط</option>
            <option value="SPECIFIC_LARGE_HALL" ${ruleType === 'SPECIFIC_LARGE_HALL' ? 'selected' : ''}>قاعة كبيرة محددة</option>
            <option value="NO_HALLS_ALLOWED" ${ruleType === 'NO_HALLS_ALLOWED' ? 'selected' : ''}>لا توجد اي قاعة (منع)</option>
        </select>
        <div class="specific-hall-container" style="display: ${ruleType === 'SPECIFIC_LARGE_HALL' ? 'inline-block' : 'none'};"></div>
        <button type="button" class="remove-rule-btn">&times;</button>
    `;
    ruleDiv.appendChild(ruleHeader);

    const specificHallContainer = ruleHeader.querySelector('.specific-hall-container');

    
    const largeHallSelect = document.createElement('select');
    largeHallSelect.className = 'large-hall-select-rule';
    let optionsHTML = '<option value="">اختر قاعة كبيرة...</option>';
    availableLargeRooms.forEach(room => {
        const isSelected = hallName === room.name ? 'selected' : '';
        optionsHTML += `<option value="${room.name}" ${isSelected}>${room.name}</option>`;
    });
    largeHallSelect.innerHTML = optionsHTML;
    specificHallContainer.appendChild(largeHallSelect);

    
    const levelsContainer = document.createElement('div');
    levelsContainer.className = 'rule-levels-group';
    availableLevelsForBuilder.forEach(level => {
        const isChecked = selectedLevels.includes(level) ? 'checked' : '';
        levelsContainer.innerHTML += `
            <label><input type="checkbox" value="${level}" ${isChecked}> ${level}</label>
        `;
    });
    ruleDiv.appendChild(levelsContainer);

    
    ruleHeader.querySelector('.remove-rule-btn').addEventListener('click', () => ruleDiv.remove());
    ruleHeader.querySelector('.rule-type-select').addEventListener('change', (e) => {
        specificHallContainer.style.display = e.target.value === 'SPECIFIC_LARGE_HALL' ? 'inline-block' : 'none';
    });

    container.appendChild(ruleDiv);
}


function addDayUI(dayData = {}) {
    const tabsContainer = document.getElementById('day-tabs-container');
    const contentContainer = document.getElementById('day-content-container');
    
    // إنشاء معرف فريد لربط الزر بالمحتوى
    const dayId = 'day-content-' + Date.now();

    // 1. إنشاء زر اللسان (Tab)
    const tabButton = document.createElement('button');
    tabButton.dataset.tabTarget = `#${dayId}`; // رابط للمحتوى
    
    const daySelect = document.createElement('select');
    daySelect.className = 'day-select-in-tab'; // اسم كلاس جديد لتجنب التعارض
    const dayOptions = ['الأحد', 'الإثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'السبت'];
    daySelect.innerHTML = dayOptions.map(d => {
        const isSelected = dayData.dayName && dayData.dayName === d ? 'selected' : '';
        return `<option value="${d}" ${isSelected}>${d}</option>`;
    }).join('');
    tabButton.appendChild(daySelect);

    const removeBtn = document.createElement('span');
    removeBtn.innerHTML = '&times;';
    removeBtn.style.cssText = 'color: #dc3545; font-weight: bold; margin-right: 10px; cursor: pointer; font-size: 20px;';
    removeBtn.title = 'حذف هذا اليوم';
    tabButton.appendChild(removeBtn);
    
    tabsContainer.appendChild(tabButton);

    // 2. إنشاء حاوية المحتوى
    const contentPane = document.createElement('div');
    contentPane.id = dayId;
    contentPane.className = 'tab-pane';
    contentPane.innerHTML = `
        <div class="time-slots-container"></div>
        <button class="add-slot-btn" type="button" style="background-color: #007bff; margin-top: 10px; width:100%;">+ إضافة فترة زمنية</button>
        
    `;
    contentContainer.appendChild(contentPane);

    const slotsContainer = contentPane.querySelector('.time-slots-container');

    // 3. ربط الأحداث
    tabButton.addEventListener('click', () => {
        // إزالة active من كل الأزرار والمحتويات
        tabsContainer.querySelectorAll('button').forEach(btn => btn.classList.remove('active'));
        contentContainer.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));
        // إضافة active للزر والمحتوى المختار
        tabButton.classList.add('active');
        contentPane.classList.add('active');
    });
    
    removeBtn.addEventListener('click', (e) => {
        e.stopPropagation(); // منع تفعيل اللسان عند الحذف
        if (confirm(`هل أنت متأكد من حذف يوم ${daySelect.value}؟`)) {
            tabButton.remove();
            contentPane.remove();
            // تفعيل اللسان الأول إذا كان موجوداً
            const firstTab = tabsContainer.querySelector('button');
            if (firstTab) firstTab.click();
        }
    });

    contentPane.querySelector('.add-slot-btn').addEventListener('click', () => addSlotUI(slotsContainer));
    

    // ملء الفترات الزمنية المحفوظة
    if (dayData.slots && Object.keys(dayData.slots).length > 0) {
        for (const time in dayData.slots) {
            const slotInfo = dayData.slots[time];
            addSlotUI(slotsContainer, { time: time, rules: slotInfo.rules, pinnedCourseId: slotInfo.pinnedCourseId });
        }
    }

    // تفعيل اللسان الجديد تلقائياً
    tabButton.click();
}


function duplicateDay(sourceDayPane) {
    const dayName = document.querySelector(`button[data-tab-target="#${sourceDayPane.id}"] .day-select-in-tab`).value;
    const sourceSlots = {};

    sourceDayPane.querySelectorAll('.time-slot-block').forEach(slotDiv => {
        const times = slotDiv.querySelectorAll('input[type="time"]');
        const timeKey = `${times[0].value}-${times[1].value}`;
        
        // === بداية التعديل: إنشاء كائن بالهيكل الجديد ===
        const slotInfo = {
            rules: [],
            pinnedCourseId: null
        };

        // 1. قراءة القواعد كالمعتاد
        slotDiv.querySelectorAll('.rule-block').forEach(ruleDiv => {
            const ruleType = ruleDiv.querySelector('.rule-type-select').value;
            const rule = {
                rule_type: ruleType,
                levels: Array.from(ruleDiv.querySelectorAll('.rule-levels-group input:checked')).map(cb => cb.value)
            };
            if (ruleType === 'SPECIFIC_LARGE_HALL') {
                rule.hall_name = ruleDiv.querySelector('.large-hall-select-rule').value;
            }
            slotInfo.rules.push(rule);
        });

        // 2. قراءة المادة المثبتة من القائمة المنسدلة الجديدة
        const pinnedSelect = slotDiv.querySelector('.pinned-course-select');
        if (pinnedSelect && pinnedSelect.value) {
            slotInfo.pinnedCourseId = parseInt(pinnedSelect.value, 10);
        }

        // 3. حفظ الكائن بالكامل
        sourceSlots[timeKey] = slotInfo;
        // === نهاية التعديل ===
    });

    addDayUI({ dayName, slots: sourceSlots });
}

function setupScheduleBuilder() {
    document.getElementById('add-day-button').addEventListener('click', () => addDayUI());

    document.getElementById('save-settings-button').addEventListener('click', () => {
        // ✨ نستدعي الدالة الموحدة والمحدثة التي تجمع كل الإعدادات
        const settingsToSave = collectAllCurrentSettings();

        // نرسل كل الإعدادات المجمعة إلى الخادم
        fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settingsToSave)
        })
        .then(res => res.json())
        .then(data => {
            if(data.success) {
                alert(data.message);
            } else {
                alert('فشل حفظ الإعدادات: ' + (data.error || 'خطأ غير معروف'));
            }
        })
        .catch(err => {
            console.error("Save Settings Error:", err);
            alert('حدث خطأ غير متوقع أثناء الاتصال بالخادم.');
        });
    });
}

function setupBackupRestoreListeners() {
    const backupBtn = document.getElementById('backup-btn');
    const restoreBtn = document.getElementById('restore-btn');
    const fileInput = document.getElementById('restore-file-input');
    const resetBtn = document.getElementById('reset-all-btn');

    backupBtn.addEventListener('click', async () => {
        try {
            const response = await fetch('/api/backup');
            if (!response.ok) throw new Error('فشل النسخ الاحتياطي من الخادم');

            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `schedule_backup_${new Date().toISOString().slice(0, 10)}.json`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
            alert('تم تصدير النسخة الاحتياطية بنجاح.');
        } catch (error) {
            alert('حدث خطأ أثناء تصدير النسخة الاحتياطية.');
            console.error(error);
        }
    });

    restoreBtn.addEventListener('click', () => {
        fileInput.click();
    });

    fileInput.addEventListener('change', (event) => {
        const file = event.target.files[0];
        if (!file) return;

        if (!confirm("هل أنت متأكد من استعادة البيانات من هذا الملف؟ سيتم الكتابة فوق جميع البيانات والإعدادات الحالية.")) {
            fileInput.value = ''; 
            return;
        }

        const reader = new FileReader();
        reader.onload = (e) => {
            try {
                const data = JSON.parse(e.target.result);
                fetch('/api/restore', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data),
                })
                .then(response => response.json())
                .then(res => {
                    if(res.success){
                        alert(res.message);
                        location.reload();
                    } else {
                        throw new Error(res.error);
                    }
                })
                .catch(err => alert(`خطأ: ${err.message}`));
            } catch (error) {
                alert('خطأ في قراءة الملف. يرجى التأكد من أنه ملف نسخة احتياطية صالح.');
                console.error(error);
            }
        };
        reader.readAsText(file);
        fileInput.value = ''; 
    });

    resetBtn.addEventListener('click', () => {
        if (confirm("تحذير! هل أنت متأكد تماماً؟ سيؤدي هذا إلى حذف جميع البيانات والإعدادات بشكل نهائي.")) {
            fetch('/api/reset-all', { method: 'POST' })
            .then(response => response.json())
            .then(res => {
                 if(res.success){
                    alert(res.message);
                    location.reload();
                } else {
                    throw new Error(res.error);
                }
            })
            .catch(err => alert(`خطأ: ${err.message}`));
        }
    });

    const shutdownBtn = document.getElementById('shutdown-btn');
    shutdownBtn.addEventListener('click', () => {
        if (confirm("هل أنت متأكد من أنك تريد إيقاف تشغيل الخادم؟ سيتم إغلاق البرنامج.")) {
            fetch('/shutdown', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        alert("تم إرسال أمر إيقاف التشغيل. سيتم إغلاق البرنامج الآن.");
                        // يمكنك إغلاق نافذة المتصفح تلقائيًا إذا أردت
                        // window.close(); 
                    }
                })
                .catch(err => {
                    console.error('Shutdown error:', err);
                    alert("فشل إرسال أمر إيقاف التشغيل.");
                });
        }
    });
}


function loadSettingsAndBuildUI() {
    fetch('/api/settings')
    .then(res => res.ok ? res.json() : null)
    .then(settings => {
        if (!settings) return;

        // استعادة إعدادات المرحلة الخامسة
        if (settings.phase_5_settings) {
            const p5s = settings.phase_5_settings;
            if (p5s.rest_periods) {
                document.getElementById('tuesday-evening-rest').checked = p5s.rest_periods.tuesday_evening || false;
                document.getElementById('thursday-evening-rest').checked = p5s.rest_periods.thursday_evening || false;
            }
            
            if (p5s.special_constraints) {
                setTimeout(() => { 
                    for (const teacher in p5s.special_constraints) {
                        const constraints = p5s.special_constraints[teacher];
                        for (const key in constraints) {
                            if (key === 'distribution_rule') {
                                const select = document.querySelector(`select[data-teacher="${teacher}"][data-rule-type="distribution"]`);
                                if (select) select.value = constraints[key];
                            } else {
                                const checkbox = document.querySelector(`input[data-teacher="${teacher}"][data-constraint="${key}"]`);
                                if (checkbox) checkbox.checked = true;
                            }
                        }
                    }
                }, 100);
            }

            if (p5s.manual_days) {
                setTimeout(() => {
                    for (const teacher in p5s.manual_days) {
                        p5s.manual_days[teacher].forEach(day => {
                            const checkbox = document.querySelector(`input[data-teacher="${teacher}"][data-day="${day}"]`);
                            if (checkbox) checkbox.checked = true;
                        });
                    }
                }, 100); 
            }
            // --- الجزء الجديد: استعادة أساتذة السبت ---
            if (p5s.saturday_teachers) {
                 setTimeout(() => {
                    p5s.saturday_teachers.forEach(teacherName => {
                        const checkbox = document.querySelector(`#saturday-teachers-checkbox-container input[value="${teacherName}"]`);
                        if(checkbox) checkbox.checked = true;
                    });
                }, 150); // تأخير طفيف لضمان ملء القائمة أولاً
            }
            
            if (p5s.last_slot_restrictions) {
                setTimeout(() => {
                    for (const teacherName in p5s.last_slot_restrictions) {
                        const restrictionValue = p5s.last_slot_restrictions[teacherName];
                        const select = document.querySelector(`.last-slot-restriction-select[data-teacher-name="${teacherName}"]`);
                        if (select) {
                            select.value = restrictionValue;
                        }
                    }
                }, 150);
            }

            // --- بداية الكود المضاف ---
            if (p5s.specific_small_room_assignments) {
                setTimeout(() => {
                    const container = document.getElementById('specific-small-room-assignments-container');
                    container.innerHTML = ''; // مسح أي صفوف قديمة
                    for (const courseName in p5s.specific_small_room_assignments) {
                        const roomName = p5s.specific_small_room_assignments[courseName];
                        addSpecificRoomAssignmentRow(container, { course: courseName, room: roomName });
                    }
                }, 150); // تأخير طفيف لضمان بناء العناصر الأخرى أولاً
            }
            // --- نهاية الكود المضاف ---

            if (p5s.level_specific_large_rooms) {
                setTimeout(() => { // نستخدم تأخيراً بسيطاً لضمان أن القوائم قد تم إنشاؤها
                    for (const levelName in p5s.level_specific_large_rooms) {
                        const roomName = p5s.level_specific_large_rooms[levelName];
                        const select = document.querySelector(`#level-large-room-assignment-container select[data-level="${levelName}"]`);
                        if (select) {
                            select.value = roomName;
                        }
                    }
                }, 150);
            }
        }

        // استعادة هيكل الجدول
        if (settings.schedule_structure) {
            const structure = settings.schedule_structure;
                        
                        // === بداية الإصلاح: استهداف الحاويات الجديدة لنظام الألسنة ===
            const tabsContainer = document.getElementById('day-tabs-container');
            const contentContainer = document.getElementById('day-content-container');
            
            // مسح الهيكل الحالي من الحاويات الصحيحة
            tabsContainer.innerHTML = ''; 
            contentContainer.innerHTML = '';
                        // === نهاية الإصلاح ===

            const dayOrder = ['السبت', 'الأحد', 'الإثنين', 'الثلاثاء', 'الأربعاء', 'الخميس'];
            const sortedDays = Object.keys(structure).sort((a, b) => dayOrder.indexOf(a) - dayOrder.indexOf(b));
            for (const dayName of sortedDays) {
            addDayUI({ dayName: dayName, slots: structure[dayName] });
            }
        }

        // --- الجزء الجديد: استعادة إعدادات الخوارزميات ---
        if(settings.algorithm_settings) {
            const algo = settings.algorithm_settings;
            const radio = document.querySelector(`input[name="scheduling_method"][value="${algo.method}"]`);
            if(radio) {
                radio.checked = true;
                // تفعيل إظهار حاوية الإعدادات الصحيحة
                radio.dispatchEvent(new Event('change'));
            }
            // ملء الحقول بالقيم المحفوظة
            document.getElementById('timeout-input').value = algo.timeout || 30;
            document.getElementById('tabu-iterations-input').value = algo.tabu_iterations || 1000;
            document.getElementById('tabu-tenure-input').value = algo.tabu_tenure || 10;
            document.getElementById('tabu-neighborhood-size-input').value = algo.tabu_neighborhood_size || 50;
            document.getElementById('ga-population-input').value = algo.ga_population_size || 50;
            document.getElementById('ga-generations-input').value = algo.ga_generations || 200;
            document.getElementById('ga-mutation-input').value = algo.ga_mutation_rate || 5;
            document.getElementById('ga-elitism-input').value = algo.ga_elitism_count || 2;
            document.getElementById('lns-iterations-input').value = algo.lns_iterations || 500;
            document.getElementById('lns-ruin-factor-input').value = algo.lns_ruin_factor || 20;
            document.getElementById('vns-iterations-input').value = algo.vns_iterations || 300;
            document.getElementById('vns-k-max-input').value = algo.vns_k_max || 10;
            document.getElementById('ma-population-input').value = algo.ma_population_size || 40;
            document.getElementById('ma-generations-input').value = algo.ma_generations || 100;
            document.getElementById('ma-mutation-input').value = algo.ma_mutation_rate || 10;
            document.getElementById('ma-elitism-input').value = algo.ma_elitism_count || 2;
            document.getElementById('ma-local-search-iterations').value = algo.ma_local_search_iterations || 5;
            document.getElementById('clonalg-population-input').value = algo.clonalg_population_size || 50;
            document.getElementById('clonalg-generations-input').value = algo.clonalg_generations || 100;
            document.getElementById('clonalg-selection-input').value = algo.clonalg_selection_size || 10;
            document.getElementById('clonalg-clone-factor-input').value = algo.clonalg_clone_factor || 1.0;
            document.getElementById('hh-iterations-input').value = algo.hh_iterations || 50;
            if (algo.hh_budget_mode) {
                const budgetRadio = document.querySelector(`input[name="hh_budget_mode"][value="${algo.hh_budget_mode}"]`);
                if (budgetRadio) {
                    budgetRadio.checked = true;
                }
            }

            // استعادة قيم الميزانيات ومدة الحظر
            document.getElementById('hh-time-budget-input').value = algo.hh_time_budget || 5;
            document.getElementById('hh-llh-iterations-input').value = algo.hh_llh_iterations || 30;
            document.getElementById('hh-tabu-tenure-input').value = algo.hh_tabu_tenure || 3;

            // استعادة الخوارزميات المختارة
            if (algo.hh_selected_llh) {
                // أولاً، قم بإلغاء تحديد كل الخيارات
                document.querySelectorAll('input[name="hh_llh_select"]').forEach(cb => cb.checked = false);
                // ثانيًا، قم بتحديد الخيارات المحفوظة فقط
                algo.hh_selected_llh.forEach(llh_name => {
                    const checkbox = document.querySelector(`input[name="hh_llh_select"][value="${llh_name}"]`);
                    if (checkbox) {
                        checkbox.checked = true;
                    }
                });
            }
            if (algo.hh_selected_llh) {
                // أولاً، قم بإلغاء تحديد كل الخانات
                document.querySelectorAll('input[name="hh_llh_select"]').forEach(cb => cb.checked = false);
                
                // ثانيًا، حدد فقط الخانات الموجودة في القائمة المحفوظة
                algo.hh_selected_llh.forEach(heuristic_key => {
                    const checkbox = document.querySelector(`input[name="hh_llh_select"][value="${heuristic_key}"]`);
                    if (checkbox) {
                        checkbox.checked = true;
                    }
                });
            } else {
                // إذا لم تكن الإعدادات محفوظة، حدد الكل كافتراضي
                document.querySelectorAll('input[name="hh_llh_select"]').forEach(cb => cb.checked = true);
            }
            document.getElementById('max-sessions-per-day-select').value = algo.max_sessions_per_day || 'none';
            if (algo.distribution_rule_type) {
                document.querySelector(`input[name="distribution_rule_type"][value="${algo.distribution_rule_type}"]`).checked = true;
            }
            if (algo.consecutive_large_hall_rule) {
                document.getElementById('consecutive-large-hall-select').value = algo.consecutive_large_hall_rule;
            }
        }

        if (settings.flexible_categories) {
            setTimeout(() => {
                const container = document.getElementById('flex-categories-container');
                if (container) {
                    container.innerHTML = ''; // مسح أي فئات قديمة
                    settings.flexible_categories.forEach(categoryData => {
                        createFlexCategoryUI(categoryData);
                    });
                }
            }, 200); // تأخير بسيط لضمان أن كل شيء آخر قد تم تحميله
        }

    })
    .catch(err => console.error("لم يتم العثور على إعدادات سابقة أو حدث خطأ.", err));
}


function collectScheduleStructureFromUI() {
    const scheduleStructure = {};
    document.querySelectorAll('.tab-content .tab-pane').forEach(dayDiv => {
         const dayName = document.querySelector(`button[data-tab-target="#${dayDiv.id}"] .day-select-in-tab`).value;
        if (!scheduleStructure[dayName]) {
            scheduleStructure[dayName] = {};
        }

        dayDiv.querySelectorAll('.time-slot-block').forEach(slotDiv => {
            const times = slotDiv.querySelectorAll('input[type="time"]');
            const timeKey = `${times[0].value}-${times[1].value}`;
            
            // === بداية التعديل: تغيير هيكل الفترة الزمنية ليقبل القواعد والمادة المثبتة ===
            if (!scheduleStructure[dayName][timeKey]) {
                scheduleStructure[dayName][timeKey] = {
                    rules: [],
                    pinnedCourseId: null 
                };
            }

            // قراءة المادة المثبتة
            const pinnedSelect = slotDiv.querySelector('.pinned-course-select');
            if (pinnedSelect && pinnedSelect.value) {
                scheduleStructure[dayName][timeKey].pinnedCourseId = parseInt(pinnedSelect.value, 10);
            }
            // === نهاية التعديل ===

            slotDiv.querySelectorAll('.rule-block').forEach(ruleDiv => {
                const ruleType = ruleDiv.querySelector('.rule-type-select').value;
                const rule = {
                    rule_type: ruleType,
                    levels: Array.from(ruleDiv.querySelectorAll('.rule-levels-group input:checked')).map(cb => cb.value)
                };

                if (ruleType === 'SPECIFIC_LARGE_HALL') {
                    rule.hall_name = ruleDiv.querySelector('.large-hall-select-rule').value;
                }
                
                // === تعديل: إضافة القاعدة إلى مصفوفة القواعد داخل الكائن ===
                if(rule.levels.length > 0) {
                   scheduleStructure[dayName][timeKey].rules.push(rule);
                }
            });
        });
    });
    return scheduleStructure;
}

function updateAllLargeRoomDropdowns() {
    document.querySelectorAll('.large-room-select').forEach(dropdown => {
        const previouslySelected = dropdown.value;
        let optionsHTML = '<option value="">اختياري: حجز قاعة كبيرة</option>';
        availableLargeRooms.forEach(room => {
            optionsHTML += `<option value="${room.name}">${room.name}</option>`;
        });
        dropdown.innerHTML = optionsHTML;
        dropdown.value = previouslySelected;
    });
}

function setupDataImportExportListeners() {
    const exportBtn = document.getElementById('export-template-btn');
    const importBtn = document.getElementById('import-data-btn');
    const fileInput = document.getElementById('import-file-input');

    exportBtn.addEventListener('click', async () => {
        try {
            const response = await fetch('/api/data-template');
            if (!response.ok) throw new Error('فشل تصدير القالب من الخادم');
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'قالب_بيانات_الجدول.xlsx';
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        } catch (error) {
            alert('حدث خطأ أثناء تصدير ملف القالب.');
            console.error(error);
        }
    });

    importBtn.addEventListener('click', () => {
        fileInput.click();
    });

    fileInput.addEventListener('change', (event) => {
        const file = event.target.files[0];
        if (!file) return;

        if (!confirm("هل أنت متأكد من استيراد البيانات من هذا الملف؟ سيتم إضافة البيانات الجديدة فقط ولن يتم حذف البيانات الحالية.")) {
            fileInput.value = ''; 
            return;
        }

        const formData = new FormData();
        formData.append('file', file);

        fetch('/api/import-data', {
            method: 'POST',
            body: formData,
        })
        .then(response => response.json())
        .then(data => {
            if(data.success) {
                alert(data.message);
                location.reload();
            } else {
                throw new Error(data.error);
            }
        })
        .catch(error => {
            alert(`حدث خطأ: ${error.message}`);
        })
        .finally(() => {
            fileInput.value = '';
        });
    });
}

function setupSettingsManagementListeners() {
    const saveAsBtn = document.getElementById('save-current-settings-as-btn');
    const loadSelectedBtn = document.getElementById('load-selected-settings-btn');
    const deleteSelectedBtn = document.getElementById('delete-selected-settings-btn');
    const settingsDropdown = document.getElementById('saved-settings-dropdown');

    // تحميل أسماء الإعدادات المحفوظة عند بدء التشغيل
    loadSavedSettingsNames();

    saveAsBtn.addEventListener('click', async () => {
        const settingsName = prompt('أدخل اسمًا لهذه المجموعة من الإعدادات:');
        if (settingsName && settingsName.trim()) {
            const currentSettings = collectAllCurrentSettings();
            try {
                const response = await fetch('/api/settings/save_as', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: settingsName.trim(), settings: currentSettings })
                });
                const data = await response.json();
                if (data.success) {
                    alert(data.message);
                    loadSavedSettingsNames(); // تحديث القائمة المنسدلة
                } else {
                    alert('فشل الحفظ: ' + data.error);
                }
            } catch (error) {
                console.error('Error saving settings:', error);
                alert('حدث خطأ أثناء حفظ الإعدادات.');
            }
        }
    });

    settingsDropdown.addEventListener('change', () => {
        const selectedName = settingsDropdown.value;
        loadSelectedBtn.disabled = !selectedName;
        deleteSelectedBtn.disabled = !selectedName;
    });

    loadSelectedBtn.addEventListener('click', async () => {
        const selectedName = settingsDropdown.value;
        if (!selectedName) return;

        if (!confirm(`هل أنت متأكد من استعادة الإعدادات باسم "${selectedName}"؟ سيتم الكتابة فوق الإعدادات الحالية.`)) {
            return;
        }

        try {
            const response = await fetch(`/api/settings/load_named?name=${encodeURIComponent(selectedName)}`);
            const settings = await response.json();
            if (response.ok) {
                applySettingsToUI(settings);
                alert(`تم استعادة الإعدادات باسم "${selectedName}" بنجاح.`);
            } else {
                alert('فشل الاستعادة: ' + settings.error);
            }
        } catch (error) {
            console.error('Error loading settings:', error);
            alert('حدث خطأ أثناء استعادة الإعدادات.');
        }
    });

    deleteSelectedBtn.addEventListener('click', async () => {
        const selectedName = settingsDropdown.value;
        if (!selectedName) return;

        if (!confirm(`هل أنت متأكد من حذف الإعدادات باسم "${selectedName}"؟ لا يمكن التراجع عن هذا الإجراء.`)) {
            return;
        }

        try {
            const response = await fetch('/api/settings/delete_named', {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: selectedName })
            });
            const data = await response.json();
            if (data.success) {
                alert(data.message);
                loadSavedSettingsNames(); // تحديث القائمة المنسدلة
                settingsDropdown.value = ""; // إعادة تعيين التحديد
                loadSelectedBtn.disabled = true;
                deleteSelectedBtn.disabled = true;
            } else {
                alert('فشل الحذف: ' + data.error);
            }
        } catch (error) {
            console.error('Error deleting settings:', error);
            alert('حدث خطأ أثناء حذف الإعدادات.');
        }
    });
}

async function loadSavedSettingsNames() {
    const settingsDropdown = document.getElementById('saved-settings-dropdown');
    settingsDropdown.innerHTML = '<option value="">-- اختر إعدادات محفوظة --</option>'; // إعادة تعيين

    try {
        const response = await fetch('/api/settings/get_saved_names');
        const names = await response.json();
        names.forEach(name => {
            const option = document.createElement('option');
            option.value = name;
            option.textContent = name;
            settingsDropdown.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading saved settings names:', error);
        // لا تعرض تنبيه للمستخدم هنا، فقط سجل الخطأ
    }
}

function populateDashboard(data) {
    // 1. الإحصائيات العامة (تبقى كما هي)
    const generalList = document.getElementById('general-stats-list');
    generalList.innerHTML = ''; 
    fetch('/api/dashboard-stats')
        .then(res => res.json())
        .then(stats => {
            generalList.innerHTML = `
                <li><span>عدد الأساتذة:</span> <span class="value">${stats.teachers_count}</span></li>
                <li><span>عدد المقررات:</span> <span class="value">${stats.courses_count}</span></li>
                <li><span>عدد القاعات:</span> <span class="value">${stats.rooms_count}</span></li>
                <li><span>عدد المستويات:</span> <span class="value">${stats.levels_count}</span></li>
            `;
        });

    // 2. حالات الفشل (تم تعديلها لتعرض جدولاً صغيراً)
    const failureTable = document.getElementById('failure-stats-list');
    failureTable.innerHTML = '';
    if (data.failures && data.failures.length > 0) {
        // عرض أول 5 أخطاء فقط
        data.failures.slice(0, 5).forEach(fail => { 
            const row = failureTable.insertRow();
            const cell1 = row.insertCell();
            const cell2 = row.insertCell();
            cell1.textContent = fail.teacher_name;
            cell2.textContent = fail.reason.substring(0, 40) + '...'; // سبب مختصر
            cell2.title = fail.reason; // السبب الكامل عند المرور
        });
    } else {
        failureTable.innerHTML = '<tr><td>لا توجد حالات فشل.</td></tr>';
    }

    // 3. إحصائيات العبء (تبقى كما هي)
    const mostBurdenedList = document.getElementById('most-burdened-list');
    const leastBurdenedList = document.getElementById('least-burdened-list');
    mostBurdenedList.innerHTML = '';
    leastBurdenedList.innerHTML = '';

    if (data.burden_stats && data.burden_stats.length > 0) {
        data.burden_stats.slice(0, 3).forEach(item => {
            mostBurdenedList.innerHTML += `<li><span>${item[0]}</span> <span class="value">${item[1]} حصص</span></li>`;
        });
        data.burden_stats.slice(-3).reverse().forEach(item => {
            leastBurdenedList.innerHTML += `<li><span>${item[0]}</span> <span class="value">${item[1]} حصص</span></li>`;
        });
    } else {
        mostBurdenedList.innerHTML = '<li>لا توجد بيانات.</li>';
        leastBurdenedList.innerHTML = '<li>لا توجد بيانات.</li>';
    }
    // --- بداية الإضافة الجديدة: ملء كرت عدد المواد لكل مستوى ---
    const levelCountsList = document.getElementById('level-counts-list');
    levelCountsList.innerHTML = ''; // مسح المحتوى القديم

    if (data.level_counts && data.level_counts.length > 0) {
        data.level_counts.forEach(item => {
            levelCountsList.innerHTML += `<li><span>${item.level}</span> <span class="value">${item.count} مادة</span></li>`;
        });
    } else {
        levelCountsList.innerHTML = '<li>لا توجد بيانات.</li>';
    }
    // --- نهاية الإضافة الجديدة ---
    // --- بداية الإضافة الثانية: ملء كرت عدد المواد الموزعة فعلياً ---
    const placedLevelCountsList = document.getElementById('placed-level-counts-list');
    placedLevelCountsList.innerHTML = ''; // مسح المحتوى القديم

    if (data.placed_level_counts && data.placed_level_counts.length > 0) {
        data.placed_level_counts.forEach(item => {
            placedLevelCountsList.innerHTML += `<li><span>${item.level}</span> <span class="value">${item.count} مادة</span></li>`;
        });
    } else {
        placedLevelCountsList.innerHTML = '<li>لم يتم توزيع أي مواد.</li>';
    }
    // --- نهاية الإضافة الثانية ---
}

function displayFailureReport(failures, unassigned_courses) {
    const reportSection = document.getElementById('failure-report-section');
    const failuresContainer = document.getElementById('scheduled-failures-table-container');
    const unassignedList = document.getElementById('unassigned-courses-list');

    failuresContainer.innerHTML = '';
    unassignedList.innerHTML = '';

    if (failures.length === 0 && unassigned_courses.length === 0) {
        reportSection.style.display = 'none';
        return;
    }

    reportSection.style.display = 'block';

    // بناء جدول الأخطاء والتعارضات
    if (failures.length > 0) {
        const table = document.createElement('table');
        table.className = 'management-table'; // يمكنك استخدام تنسيق جدول موجود أو إضافة تنسيق جديد
        const thead = table.createTHead();
        const headerRow = thead.insertRow();
        headerRow.innerHTML = '<th>المادة/القيد</th><th>الأستاذ/المستوى</th><th>السبب التفصيلي</th>';

        const tbody = table.createTBody();
        failures.forEach(fail => {
            const row = tbody.insertRow();
            row.insertCell().textContent = fail.course_name || 'N/A';
            row.insertCell().textContent = fail.teacher_name || 'N/A';
            row.insertCell().textContent = fail.reason || 'غير محدد';
        });
        failuresContainer.appendChild(table);
    } else {
        failuresContainer.innerHTML = '<p>لا توجد حالات فشل. تم توزيع كل المواد المسندة بنجاح والتزم بكل القيود.</p>';
    }

    // بناء قائمة المواد غير المسندة (تبقى كما هي)
    if (unassigned_courses.length > 0) {
        unassigned_courses.forEach(course => {
            const li = document.createElement('li');
            li.innerHTML = `<span><strong>المادة:</strong> ${course.name} (المستوى: ${course.levels.join('، ')})</span>`;
            unassignedList.appendChild(li);
        });
    } else {
        unassignedList.innerHTML = '<li>كل المواد مسندة لأساتذة.</li>';
    }
}

function populateSaturdayTeachersSelect(teachers) {
    const container = document.getElementById('saturday-teachers-checkbox-container');
    if (!container) return;
    container.innerHTML = '';
    teachers.forEach(teacher => {
        const label = document.createElement('label');
        label.style.display = 'block';
        label.style.cursor = 'pointer';
        
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = teacher.name;
        checkbox.style.marginLeft = '8px';
        
        label.appendChild(checkbox);
        label.appendChild(document.createTextNode(teacher.name));
        
        container.appendChild(label);
    });
}

function populateLastSlotRestrictionUI(teachers) {
    const container = document.getElementById('last-slot-restriction-container');
    if (!container) return;
    container.innerHTML = '';

    const table = document.createElement('table');
    table.style.width = '100%';
    
    teachers.forEach(teacher => {
        const row = table.insertRow();
        
        // الخلية الخاصة باسم الأستاذ
        const nameCell = row.insertCell();
        nameCell.textContent = teacher.name;
        nameCell.style.width = '15%'; // إعطاء مساحة أكبر للاسم
        nameCell.style.textAlign = 'right'; // محاذاة الاسم إلى اليمين
        nameCell.style.paddingLeft = '20px'; // إضافة هامش أيسر

        // الخلية الخاصة بالقائمة المنسدلة
        const optionsCell = row.insertCell();
        optionsCell.style.textAlign = 'right'; // محاذاة القائمة إلى اليسار
        optionsCell.innerHTML = `
            <select class="last-slot-restriction-select" data-teacher-name="${teacher.name}">
                <option value="none" selected>لا يوجد قيد</option>
                <option value="last_1">منع آخر حصة</option>
                <option value="last_2">منع آخر حصتين</option>
            </select>
        `;
    });
    container.appendChild(table);
}

function populateLevelLargeRoomAssignment(levels, largeRooms) {
    const container = document.getElementById('level-large-room-assignment-container');
    if (!container) return;
    container.innerHTML = '';

    if (levels.length === 0) {
        container.innerHTML = '<p>الرجاء إضافة مستويات دراسية أولاً.</p>';
        return;
    }

    if (largeRooms.length === 0) {
        container.innerHTML = '<p>لا توجد قاعات كبيرة متاحة ليتم تخصيصها.</p>';
        return;
    }

    const table = document.createElement('table');
    table.style.width = '100%';
    table.style.borderCollapse = 'collapse';

    levels.forEach(level => {
        const row = table.insertRow();
        const levelCell = row.insertCell();
        levelCell.textContent = level;
        levelCell.style.width = '40%';
        levelCell.style.padding = '8px';
        levelCell.style.borderBottom = '1px solid #eee';

        const selectCell = row.insertCell();
        selectCell.style.borderBottom = '1px solid #eee';

        const select = document.createElement('select');
        select.dataset.level = level;
        select.className = 'level-large-room-select';
        select.style.width = '100%';
        select.style.padding = '5px';

        let optionsHTML = '<option value="">-- لا يوجد تفضيل --</option>';
        largeRooms.forEach(room => {
            optionsHTML += `<option value="${room.name}">${room.name}</option>`;
        });
        select.innerHTML = optionsHTML;

        selectCell.appendChild(select);
    });

    container.appendChild(table);
}

function populateConsecutiveHallDropdown() {
    const select = document.getElementById('consecutive-large-hall-select');
    if (!select) return;

    // الاحتفاظ بالخيارات الافتراضية ومسح أسماء القاعات القديمة
    while (select.options.length > 2) {
        select.remove(2);
    }

    availableLargeRooms.forEach(room => {
        const option = document.createElement('option');
        option.value = room.name;
        option.textContent = `منع التوالي في ( ${room.name} ) فقط`;
        select.appendChild(option);
    });
}