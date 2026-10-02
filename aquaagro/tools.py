"""Tools extracted from notebook cell 25; no gold answers are exposed."""
import math
from datetime import date, datetime, timedelta
from typing import Literal
from langchain_core.tools import tool


def build_tools(store, enforce_freshness=True):
    db_records = store.db_records
    db_meta = store.db_meta
    ENFORCE_FRESHNESS = enforce_freshness
    def refusal(reason):
        return {'type': 'refusal', 'value': 'отказ', 'unit': None, 'explanation': reason}

    def numeric(value, unit, explanation, sources):
        return {'type': 'numeric', 'value': round(float(value), 2), 'unit': unit, 'explanation': explanation, 'sources': sources}

    def category(value, explanation, sources):
        return {'type': 'status', 'value': value, 'unit': None, 'explanation': explanation, 'sources': sources}

    def field_record(field_id):
        return next((r for r in db_records('fields') if r['field_id'] == field_id), None)

    @tool
    def project_info() -> dict:
        """Вернуть участки, площади, время снимка и учебные пороги.
        Данные искусственные. Не возвращает оценочные вопросы и эталоны."""
        return {'info': db_meta('info'), 'policy': db_meta('policy'), 'fields': db_records('fields')}

    @tool
    def water_calculation(field_ids: list[str], start_date: str, end_date: str, operation: Literal['sum', 'mean_daily', 'per_hectare', 'difference', 'percent_change']='sum') -> dict:
        """Рассчитать расход воды по SQLite за включительный период.
        Даты строго YYYY-MM-DD. sum: сумма для указанных участков;
        mean_daily и per_hectare: один участок;
        difference: сумма первого участка минус сумма второго;
        percent_change: один участок, объём в конечный день относительно
        начального дня. Отсутствующие записи не заменяются нулём."""
        try:
            begin, end = (date.fromisoformat(start_date), date.fromisoformat(end_date))
        except ValueError:
            return refusal('Дата должна иметь формат YYYY-MM-DD.')
        if begin > end or (end - begin).days > 366:
            return refusal('Некорректный или слишком длинный период.')
        if not field_ids or len(set(field_ids)) != len(field_ids):
            return refusal('Нужен непустой список различных участков.')
        if any((field_record(fid) is None for fid in field_ids)):
            return refusal('Участок отсутствует в справочнике.')
        if operation == 'difference' and len(field_ids) != 2:
            return refusal('Для разницы нужны два участка в заданном порядке.')
        if operation in {'mean_daily', 'per_hectare', 'percent_change'}:
            if len(field_ids) != 1:
                return refusal('Для этой операции нужен один участок.')
        days = [(begin + timedelta(days=i)).isoformat() for i in range((end - begin).days + 1)]
        needed_days = list(dict.fromkeys([start_date, end_date])) if operation == 'percent_change' else days
        rows = db_records('water_daily')
        totals, selected = ({}, [])
        for fid in field_ids:
            matching = [r for r in rows if r['field_id'] == fid and r['date'] in needed_days]
            for day in needed_days:
                day_rows = [r for r in matching if r['date'] == day]
                if len(day_rows) != 1:
                    return refusal(f'На участке {fid} за {day} запись отсутствует или неоднозначна; полный расчёт невозможен.')
            if any((not math.isfinite(float(r['volume_m3'])) or r['volume_m3'] < 0 for r in matching)):
                return refusal('Обнаружен некорректный объём воды.')
            totals[fid] = sum((r['volume_m3'] for r in matching))
            selected.extend(matching)
        sources = [r['record_id'] for r in selected]
        explanation = f'Записи SQLite: {selected}.'
        if operation == 'sum':
            return numeric(sum(totals.values()), 'м³', explanation, sources)
        if operation == 'mean_daily':
            return numeric(totals[field_ids[0]] / len(days), 'м³/сутки', explanation + f' Деление на {len(days)} дней.', sources)
        if operation == 'per_hectare':
            area = field_record(field_ids[0])['area_ha']
            if area <= 0:
                return refusal('Площадь участка некорректна.')
            return numeric(totals[field_ids[0]] / area, 'м³/га', explanation + f' Деление на площадь {area} га.', sources)
        if operation == 'difference':
            return numeric(totals[field_ids[0]] - totals[field_ids[1]], 'м³', explanation + f' {field_ids[0]} минус {field_ids[1]}.', sources)
        by_day = {r['date']: r['volume_m3'] for r in selected}
        old, new = (by_day[start_date], by_day[end_date])
        if old == 0:
            return refusal('Процентное изменение от нуля не определено.')
        return numeric((new - old) / old * 100, '%', explanation + ' Расчёт: (новое − старое) / старое × 100.', sources)

    @tool
    def reading_calculation(field_id: str, metric: Literal['pressure', 'moisture'], operation: Literal['value', 'range', 'age']='value', on_date: str='') -> dict:
        """Прочитать показание SQLite и вернуть значение, категорию диапазона
        или категорию давности. on_date: YYYY-MM-DD для исторического дня;
        пустая строка означает последнее показание к времени снимка.
        Границы диапазона и допустимой давности включены."""
        if field_record(field_id) is None:
            return refusal('Участок отсутствует в справочнике.')
        as_of = datetime.fromisoformat(db_meta('info')['as_of'])
        policy = db_meta('policy')
        if on_date:
            try:
                date.fromisoformat(on_date)
            except ValueError:
                return refusal('Дата должна иметь формат YYYY-MM-DD.')
        rows = [r for r in db_records('readings') if r['field_id'] == field_id and datetime.fromisoformat(r['timestamp']) <= as_of and (not on_date or r['timestamp'][:10] == on_date)]
        if not rows:
            return refusal('Показаний за запрошенный период нет.')
        latest_time = max((r['timestamp'] for r in rows))
        latest = [r for r in rows if r['timestamp'] == latest_time]
        if len(latest) != 1:
            return refusal('Последние показания неоднозначны.')
        row = latest[0]
        age = (as_of - datetime.fromisoformat(row['timestamp'])).total_seconds() / 60
        value = row['pressure_bar'] if metric == 'pressure' else row['moisture_index']
        if not math.isfinite(float(value)):
            return refusal('Показание некорректно.')
        if metric == 'moisture' and (not 0 <= value <= 100):
            return refusal('Индекс влажности вне допустимых 0–100.')
        if metric == 'pressure' and value < 0:
            return refusal('Отрицательное давление некорректно.')
        if ENFORCE_FRESHNESS and (not on_date) and (age > policy['max_age_minutes']):
            return refusal(f"Показание устарело: {age:g} минут; допустимо не более {policy['max_age_minutes']} минут.")
        explanation = f"Запись {row['reading_id']}, время {row['timestamp']}. Давность относительно снимка: {age:g} минут."
        sources = [row['reading_id']]
        if operation == 'age':
            status = 'актуальные_данные' if age <= policy['max_age_minutes'] else 'устаревшие_данные'
            return category(status, explanation, sources)
        if operation == 'range':
            low, high = (policy['pressure_min_bar'], policy['pressure_max_bar']) if metric == 'pressure' else (policy['moisture_min'], policy['moisture_max'])
            status = 'ниже_диапазона' if value < low else 'выше_диапазона' if value > high else 'в_диапазоне'
            return category(status, explanation + f' Диапазон [{low}, {high}].', sources)
        result = numeric(value, 'бар' if metric == 'pressure' else 'пунктов индекса', explanation, sources)
        result['age_minutes'] = age
        result['max_age_minutes'] = policy['max_age_minutes']
        return result

    @tool
    def irrigation_change(field_id: str, unit: Literal['п.п.', 'пунктов индекса']='пунктов индекса') -> dict:
        """Рассчитать изменение индекса влажности в последнем событии полива:
        после минус до. Если вопрос явно просит процентные пункты,
        unit='п.п.'; если изменение индекса, unit='пунктов индекса'."""
        if field_record(field_id) is None:
            return refusal('Участок отсутствует в справочнике.')
        as_of = datetime.fromisoformat(db_meta('info')['as_of'])
        rows = [r for r in db_records('irrigation_events') if r['field_id'] == field_id and datetime.fromisoformat(r['timestamp']) <= as_of]
        if not rows:
            return refusal('Событий полива для участка нет.')
        row = max(rows, key=lambda r: r['timestamp'])
        before, after = (row['before_moisture_index'], row['after_moisture_index'])
        if not (0 <= before <= 100 and 0 <= after <= 100):
            return refusal('В событии некорректные значения влажности.')
        return numeric(after - before, unit, f"Событие {row['event_id']}: {after} − {before} = {after - before}.", [row['event_id']])

    @tool
    def unsupported_conclusion(field_id: str, request_type: Literal['leak', 'exact_irrigation', 'forecast']) -> dict:
        """Проверить возможность вывода об утечке, точной дозе полива
        или прогнозе. В текущем снимке этих сведений недостаточно."""
        if field_record(field_id) is None:
            return refusal('Участок отсутствует в справочнике.')
        reasons = {'leak': 'Низкое давление само по себе не доказывает утечку.', 'exact_irrigation': 'Нет сведений о культуре, почве и агрономических условиях для назначения точного объёма полива.', 'forecast': 'В снимке нет прогноза погоды и будущих измерений.'}
        return refusal(reasons[request_type])

    return [project_info, water_calculation, reading_calculation, irrigation_change, unsupported_conclusion]
