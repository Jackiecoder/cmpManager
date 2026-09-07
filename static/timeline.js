// Pure timeline selectors shared with the UI tests. No dates are parsed as UTC.
(function (root) {
  'use strict';
  const palette = ['#176b91', '#8c4d9d', '#197654', '#b35c19', '#bd435e', '#4260b6', '#0b7b80', '#896822'];
  function notesForProject(records, projectId, sectionId = '') {
    return records.filter(r => r.kind === 'notes' && r.project_id === projectId && r.show_on_timeline !== false
      && (!sectionId || (sectionId === 'unassigned' ? !r.subsection_id : r.subsection_id === sectionId)))
      .sort((a, b) => b.date.localeCompare(a.date)
        || (b.created_at || '').localeCompare(a.created_at || '') || a.id.localeCompare(b.id));
  }
  function sectionColors(records, projectId) {
    const sections = records.filter(r => r.kind === 'subsections' && r.project_id === projectId)
      .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
    const colors = { '': '#61768a' };
    sections.forEach((s, index) => {
      // New sections append; editing a title or switching filters never recolors others.
      colors[s.id] = palette[index] || `hsl(${((index - palette.length) * 137.508 + 12).toFixed(3)} 58% 36%)`;
    });
    return colors;
  }
  root.CmpTimeline = { notesForProject, sectionColors };
})(typeof window === 'undefined' ? globalThis : window);
