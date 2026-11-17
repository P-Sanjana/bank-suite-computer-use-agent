// ============================================================
// FNB Back-Office Shared Application State
// ============================================================
// Storage: sessionStorage (resets on tab close — intentional
// for automation replay determinism)

var STORAGE_KEY_APPS    = 'bank_suite_applications';
var STORAGE_KEY_MEMBERS = 'bank_suite_members';

// ---- Seed data -----------------------------------------

var SEED_MEMBERS = [
  {
    memberNo:    '1042381',
    firstName:   'Marcus',
    lastName:    'Delacroix',
    dob:         '04/17/1979',
    ssn4:        '4821',
    address:     '2240 Birchwood Ave, Springfield, IL 62704',
    phone:       '(217) 555-0143',
    email:       'mdelacroix@email.com',
    status:      'ACTIVE',
    memberSince: '03/2011',
    accounts:    3,
    activeCCs:   1,
    creditScore: 734,
    annualIncome:82000,
    employer:    'Midwest Manufacturing Co.',
    notes:       ''
  },
  {
    memberNo:    '2987654',
    firstName:   'Yolanda',
    lastName:    'Okonkwo',
    dob:         '11/02/1985',
    ssn4:        '9934',
    address:     '88 Maple Court, Naperville, IL 60540',
    phone:       '(630) 555-0288',
    email:       'yokonkwo@webmail.net',
    status:      'ACTIVE',
    memberSince: '07/2018',
    accounts:    2,
    activeCCs:   0,
    creditScore: 651,
    annualIncome:54000,
    employer:    'Northside School District',
    notes:       ''
  },
  {
    memberNo:    '3310042',
    firstName:   'Raymond',
    lastName:    'Strickland',
    dob:         '09/30/1961',
    ssn4:        '1177',
    address:     '517 Oak Street, Rockford, IL 61101',
    phone:       '(815) 555-0377',
    email:       '',
    status:      'ACTIVE',
    memberSince: '01/2005',
    accounts:    5,
    activeCCs:   3,
    creditScore: 588,
    annualIncome:41000,
    employer:    'Retired',
    notes:       'Income verification on file. Low score due to missed payment 2022.'
  },
  {
    memberNo:    '4001229',
    firstName:   'Priya',
    lastName:    'Subramaniam',
    dob:         '02/14/1991',
    ssn4:        '6602',
    address:     '1900 W Diversey Pkwy, Apt 4B, Chicago, IL 60614',
    phone:       '(312) 555-0411',
    email:       'psubramaniam@corp.org',
    status:      'ACTIVE',
    memberSince: '11/2021',
    accounts:    1,
    activeCCs:   0,
    creditScore: 762,
    annualIncome:118000,
    employer:    'Apex Consulting Group',
    notes:       ''
  },
  {
    // Deterministic error member: frozen account
    memberNo:    '9999001',
    firstName:   'Test',
    lastName:    'ErrorState',
    dob:         '01/01/1970',
    ssn4:        '0000',
    address:     '1 Error Lane, Test City, IL 00000',
    phone:       '(000) 000-0000',
    email:       '',
    status:      'FROZEN',
    memberSince: '01/2000',
    accounts:    0,
    activeCCs:   0,
    creditScore: 0,
    annualIncome:0,
    employer:    '',
    notes:       'TEST RECORD: Account frozen for automation error-state testing.'
  }
];

var SEED_APPLICATIONS = [
  {
    id:             'APP-1001293',
    memberNo:       '1042381',
    firstName:      'Marcus',
    lastName:       'Delacroix',
    submitted:      '09/02/2026',
    requestedLimit: 5000,
    score:          734,
    status:         'PENDING',
    assignedTo:     'JSMITH'
  },
  {
    id:             'APP-1001187',
    memberNo:       '2987654',
    firstName:      'Yolanda',
    lastName:       'Okonkwo',
    submitted:      '08/28/2026',
    requestedLimit: 2500,
    score:          651,
    status:         'PENDING',
    assignedTo:     'TLEE'
  },
  {
    id:             'APP-1001055',
    memberNo:       '3310042',
    firstName:      'Raymond',
    lastName:       'Strickland',
    submitted:      '08/20/2026',
    requestedLimit: 1000,
    score:          588,
    status:         'DECLINED',
    assignedTo:     'JSMITH',
    decidedBy:      'JSMITH',
    decidedAt:      '08/22/2026'
  },
  {
    id:             'APP-1001411',
    memberNo:       '4001229',
    firstName:      'Priya',
    lastName:       'Subramaniam',
    submitted:      '09/05/2026',
    requestedLimit: 10000,
    score:          762,
    status:         'PENDING',
    assignedTo:     ''
  },
  {
    id:             'APP-1000901',
    memberNo:       '4001229',
    firstName:      'Priya',
    lastName:       'Subramaniam',
    submitted:      '07/14/2026',
    requestedLimit: 7500,
    score:          755,
    status:         'APPROVED',
    assignedTo:     'TLEE',
    decidedBy:      'TLEE',
    decidedAt:      '07/17/2026'
  },
  {
    id:             'APP-1001502',
    memberNo:       '9999001',
    firstName:      'Test',
    lastName:       'ErrorState',
    submitted:      '09/08/2026',
    requestedLimit: 500,
    score:          0,
    status:         'ESCALATED',
    assignedTo:     'COMP01'
  }
];

// ---- Data Access ----------------------------------------

function getApplications() {
  try {
    var raw = sessionStorage.getItem(STORAGE_KEY_APPS);
    if (raw) return JSON.parse(raw);
  } catch(e) {}
  // Seed on first load
  sessionStorage.setItem(STORAGE_KEY_APPS, JSON.stringify(SEED_APPLICATIONS));
  return SEED_APPLICATIONS.slice();
}

function saveApplications(apps) {
  sessionStorage.setItem(STORAGE_KEY_APPS, JSON.stringify(apps));
}

function getMembers() {
  try {
    var raw = sessionStorage.getItem(STORAGE_KEY_MEMBERS);
    if (raw) return JSON.parse(raw);
  } catch(e) {}
  sessionStorage.setItem(STORAGE_KEY_MEMBERS, JSON.stringify(SEED_MEMBERS));
  return SEED_MEMBERS.slice();
}

// ---- Utility -------------------------------------------

function scoreClass(score) {
  if (score >= 720) return 'score-good';
  if (score >= 620) return 'score-fair';
  return 'score-poor';
}
