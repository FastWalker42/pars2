const fs = require('fs').promises

async function readJsonFile() {
	try {
		const data = await fs.readFile('./avito_items1.json', 'utf8')
		const jsonData = JSON.parse(data)
		console.log(jsonData.length)
	} catch (err) {
		console.error('Ошибка чтения:', err)
	}
}

readJsonFile()
