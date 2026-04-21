from flask import Flask
import json

app = Flask(__name__)

@app.route('/')
def dashboard():
    with open('contracts.json', 'r') as f:
        contracts = json.load(f)
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Contract Dashboard</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; }
            table { border-collapse: collapse; width: 100%; max-width: 800px; }
            th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
            th { background-color: #4CAF50; color: white; }
            tr:nth-child(even) { background-color: #f2f2f2; }
        </style>
    </head>
    <body>
        <h1>Contract Dashboard</h1>
        <table>
            <tr>
                <th>Contract</th>
                <th>Ceiling $</th>
                <th>Burn %</th>
                <th>Remaining $</th>
            </tr>
    """
    
    for contract in contracts:
        html += f"""
            <tr>
                <td>{contract['contract']}</td>
                <td>${contract['ceiling']:,}</td>
                <td>{contract['burn_percent']}%</td>
                <td>${contract['remaining']:,}</td>
            </tr>
        """
    
    html += """
        </table>
    </body>
    </html>
    """
    
    return html

if __name__ == '__main__':
    app.run(port=5000)
