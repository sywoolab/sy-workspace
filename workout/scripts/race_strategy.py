"""Render the saved race strategy for the dashboard and its notification."""
from html import escape


def strategy_text(strategy):
    lines = [strategy['title'], strategy['status']]
    for section in strategy['sections']:
        lines.append('\n' + section['title'])
        lines.extend(section['items'])
    lines.append('\n공식 자료')
    lines.extend(source['url'] for source in strategy['sources'])
    return '\n'.join(lines)


def strategy_html(strategy):
    parts = ['<div id="kobe-race-strategy">',
             '<div class="section">' + escape(strategy['title']) + '</div>',
             '<p>' + escape(strategy['status']) + '</p>']
    for section in strategy['sections']:
        parts.append('<h3>' + escape(section['title']) + '</h3><ul>')
        parts.extend('<li>' + escape(item) + '</li>' for item in section['items'])
        parts.append('</ul>')
    parts.append('<p>공식 자료: ' + ' · '.join(
        '<a href="' + escape(source['url'], quote=True) + '">' + escape(source['title']) + '</a>'
        for source in strategy['sources']) + '</p></div>')
    return '\n'.join(parts)
