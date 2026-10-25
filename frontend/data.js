// These are synthetic UI fixtures. The backend scenario registry is the source of truth.
export const destinations = [
  { id: 'FR_TOURISM', schengen: true, name: 'Франция', continent: 'Европа', icon: 'landmark', sampleEntry: 'visa', alias: 'france', hint: 'Подготовьте документы для туристической поездки' },
  { id: 'JP_TOURISM', schengen: false, name: 'Япония', continent: 'Азия', icon: 'flower', sampleEntry: 'visa', alias: 'japan', hint: 'Сверьте программу пребывания и документы' },
  { id: 'EG_TOURISM', schengen: false, name: 'Египет', continent: 'Африка', icon: 'pyramid', sampleEntry: 'evisa', alias: 'egypt', hint: 'Уточните условия въезда по маршруту' },
  { id: 'US_TOURISM', schengen: false, name: 'США', continent: 'Северная Америка', icon: 'building', sampleEntry: 'visa', alias: 'usa united states америка', hint: 'Изучите порядок официальной подачи' },
  { id: 'BR_TOURISM', schengen: false, name: 'Бразилия', continent: 'Южная Америка', icon: 'palm', sampleEntry: 'visa_free', alias: 'brazil', hint: 'Сверьте допустимый срок поездки' },
  { id: 'AU_TOURISM', schengen: false, name: 'Австралия', continent: 'Океания', icon: 'waves', sampleEntry: 'visa', alias: 'australia', hint: 'Проверьте условия туристического сценария' },
  { id: 'ES_TOURISM', schengen: true, name: 'Испания', continent: 'Европа', icon: 'sun', sampleEntry: 'visa', alias: 'spain', hint: 'Уточните требования к подаче' },
  { id: 'IN_TOURISM', schengen: false, name: 'Индия', continent: 'Азия', icon: 'landmark', sampleEntry: 'evisa', alias: 'india', hint: 'Сверьте условия электронной процедуры' },
  { id: 'ZA_TOURISM', schengen: false, name: 'ЮАР', continent: 'Африка', icon: 'mountain', sampleEntry: 'visa_free', alias: 'south africa', hint: 'Сверьте срок и цель пребывания' },
  { id: 'CA_TOURISM', schengen: false, name: 'Канада', continent: 'Северная Америка', icon: 'trees', sampleEntry: 'visa', alias: 'canada', hint: 'Уточните требования к документам' },
  { id: 'AR_TOURISM', schengen: false, name: 'Аргентина', continent: 'Южная Америка', icon: 'mountain', sampleEntry: 'visa_free', alias: 'argentina', hint: 'Проверьте правила въезда и пересадок' },
  { id: 'NZ_TOURISM', schengen: false, name: 'Новая Зеландия', continent: 'Океания', icon: 'mountain', sampleEntry: 'visa', alias: 'new zealand', hint: 'Уточните условия туристической поездки' },
];

export const entryLabels = {
  visa: 'Виза', evisa: 'Электронная виза', visa_free: 'Безвизовый сценарий', needs_verification: 'Требует проверки'
};

export const sampleProfile = {
  given_name: 'Анна', surname: 'Иванова', birth_date: '1995-04-12', citizenship: 'RU', residence_country: 'RU',
  has_other_citizenship: false, has_foreign_residence_permit: false, passport_kind: 'biometric',
  passport_issued_on: '2021-05-10', passport_expires_on: '2031-05-10', employment_status: 'employed'
};

export const sampleCase = {
  id: 'demo-france', scenario_id: 'FR_TOURISM', schengen: true, trip: {
    purpose: 'tourism', destination_country: 'FR', starts_on: '2027-06-10', ends_on: '2027-06-20',
    departure_city: 'Москва', application_country: 'RU', accommodation_type: 'hotel', funding_type: 'self'
  },
  answers: {}, revision: 1,
  readiness: { percent: null, required_total: 7, satisfied_required: 4, ready: false, unknown_required_conditions: 1, blocking_count: 1, reasons: ['Срок действия паспорта требует проверки'] },
  process: { percent: 33, current_stage: 'Подготовка документов', outcome: 'unknown', stages: [{id:'start',label:'Дело создано',done:true},{id:'documents',label:'Документы',done:true},{id:'appointment',label:'Запись',done:false},{id:'submission',label:'Подача',done:false},{id:'review',label:'Рассмотрение',done:false},{id:'decision',label:'Решение',done:false}] },
  checklist: [
    {id:'passport',kind:'document',title:'Загранпаспорт',mandatory:true,applicability:'yes',state:'needs_verification',explanation:'Проверьте срок действия относительно поездки.',next_action:'Подтвердить паспорт'},
    {id:'bank',kind:'document',title:'Подтверждение финансов',mandatory:true,applicability:'yes',state:'satisfied',explanation:'Образец документа прикреплён; требуется сверка с правилами дела.',next_action:'Проверить выписку'},
    {id:'insurance',kind:'document',title:'Страховой полис',mandatory:true,applicability:'yes',state:'missing',explanation:'Добавьте образец полиса и проверьте даты, территорию и покрытие.',next_action:'Добавить полис'},
    {id:'route',kind:'manual',title:'Маршрут поездки',mandatory:true,applicability:'yes',state:'satisfied',explanation:'Маршрут указан вручную.',next_action:'Проверить транзит'},
    {id:'accommodation',kind:'manual',title:'Проживание',mandatory:true,applicability:'yes',state:'satisfied',explanation:'Подтверждение отмечено в демо.',next_action:'Открыть требование'},
    {id:'photo',kind:'manual',title:'Фотография',mandatory:true,applicability:'yes',state:'satisfied',explanation:'Отмечено в демо.',next_action:'Открыть требование'},
    {id:'appointment',kind:'manual',title:'Официальная запись',mandatory:true,applicability:'unknown',state:'needs_verification',explanation:'Условия и место подачи должны быть сверены с источником.',next_action:'Проверить источник'}
  ],
  attached_document_ids: ['sample-passport','sample-bank'], created_at: '2026-10-25T09:00:00+03:00'
};

export const sampleDocuments = [
  {id:'sample-passport',document_type:'passport',original_filename:'SAMPLE_passport.pdf',mime_type:'application/pdf',size_bytes:246000,extraction_state:'needs_input',facts_revision:1,fields:[{key:'surname',value:'Иванова',source_kind:'ocr',confidence:0.91,user_confirmed:false},{key:'expires_on',value:'2031-05-10',source_kind:'ocr',confidence:0.78,user_confirmed:false}],quality_findings:[{severity:'warning',message:'Проверьте распознанную дату окончания паспорта.'}],created_at:'2026-10-25T09:10:00+03:00'},
  {id:'sample-bank',document_type:'bank_statement',original_filename:'SAMPLE_bank_statement.pdf',mime_type:'application/pdf',size_bytes:187000,extraction_state:'extracted',facts_revision:1,fields:[{key:'owner',value:'Анна Иванова',source_kind:'pdf_text',confidence:null,user_confirmed:true}],quality_findings:[],created_at:'2026-10-25T09:15:00+03:00'},
  {id:'sample-insurance',document_type:'insurance',original_filename:'SAMPLE_insurance.png',mime_type:'image/png',size_bytes:340000,extraction_state:'uploaded',facts_revision:0,fields:[],quality_findings:[],created_at:'2026-10-25T09:20:00+03:00'}
];

export const sampleEvents = [
  {id:'event-1',case_id:'demo-france',event_type:'form_prepared',occurred_at:'2026-10-25T10:00:00+03:00',source:'manual',note:'Создано демонстрационное дело.'},
  {id:'event-2',case_id:'demo-france',event_type:'appointment_booked',occurred_at:'2026-10-25T11:00:00+03:00',source:'manual',note:'Образец записи для демонстрации.'}
];
